/**
 * 小红书 board（收藏夹）页采集器，在页面内通过 evaluate_script 注入运行。
 *
 * 实测依据（2026-11-18）：
 * - board 卡片 section.note-item 自带标题/作者/封面/点赞/xsec_token，
 *   **不需要逐条打开笔记页**；详情页才有的发布时间、正文、话题标签、
 *   收藏/评论数在 board 页拿不到。
 * - board 是虚拟列表，DOM 常驻约 32 张，滚动后旧卡片被卸载，
 *   所以必须边滚边收，不能滚到底再读。
 * - data-index 会随收藏夹重排变化，去重只能用 data-note-id。
 *
 * 使用步骤（chrome-devtools MCP）：
 *   1) select_page 时带 bringToFront: true —— 后台标签页的 setTimeout
 *      会被 Chrome 节流到 1 分钟以上，滚动会慢两个数量级。
 *   2) evaluate_script 注入本文件全文（安装 window.__xhsBoardCollector）。
 *   3) evaluate_script 执行：
 *        await window.__xhsBoardCollector.start({ endpoint: 'http://127.0.0.1:8899/collect' })
 *      返回 {total, rounds, idle, ...}。
 *   4) 中途可用 window.__xhsBoardCollector.result() 取已收结果。
 *
 * 这是采集端；落盘与飞书同步见 scripts/xhs_board_collect.py。
 */
(function installBoardCollector() {
  if (window.__xhsBoardCollector) return 'already installed';

  var SELECTORS = {
    card: 'section.note-item',
    title: 'a.title span',
    titleFallback: 'a.title',
    author: '.author .name',
    authorLink: '.author[href]',
    cover: 'a.cover img',
    like: '.like-wrapper .count'
  };

  function text(el) {
    return el ? String(el.textContent || '').trim() : '';
  }

  function attr(el, name) {
    return el ? String(el.getAttribute(name) || '') : '';
  }

  function tokenFromHref(root) {
    var links = root.querySelectorAll('a[href]');
    for (var i = 0; i < links.length; i++) {
      var href = links[i].getAttribute('href') || '';
      var match = href.match(/[?&]xsec_token=([^&#]+)/);
      if (match) return decodeURIComponent(match[1]);
    }
    return '';
  }

  function pathToken(href) {
    var match = (href || '').match(/[?&]xsec_token=([^&#]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function lastPathSegment(href) {
    if (!href) return '';
    var path = href.split('?')[0];
    var parts = path.split('/').filter(function (part) { return part; });
    return parts.length ? parts[parts.length - 1] : '';
  }

  function parseCard(card) {
    var noteId = attr(card, 'data-note-id');
    if (!noteId) return null;

    var titleEl = card.querySelector(SELECTORS.title) ||
                  card.querySelector(SELECTORS.titleFallback);
    var authorEl = card.querySelector(SELECTORS.author);
    var authorLink = card.querySelector(SELECTORS.authorLink);
    var coverImg = card.querySelector(SELECTORS.cover);
    var likeEl = card.querySelector(SELECTORS.like);
    var authorHref = authorLink ? (authorLink.getAttribute('href') || '') : '';
    var hasVideoMarker = card.querySelector(
      'img[eagle-type="video"], .play-icon, .video-icon'
    );

    return {
      note_id: noteId,
      xsec_token: tokenFromHref(card),
      title: text(titleEl),
      author: text(authorEl),
      author_id: lastPathSegment(authorHref),
      author_token: pathToken(authorHref),
      cover: attr(coverImg, 'src'),
      cover_width: attr(coverImg, 'data-width'),
      cover_height: attr(coverImg, 'data-height'),
      likes: text(likeEl),
      is_video: !!hasVideoMarker,
      data_index: attr(card, 'data-index')
    };
  }

  var notes = new Map();
  var running = false;
  var stats = { rounds: 0, idle: 0, lastAdded: 0, scans: 0 };

  function scan() {
    var cards = document.querySelectorAll(SELECTORS.card);
    var added = 0;
    for (var i = 0; i < cards.length; i++) {
      var note = parseCard(cards[i]);
      if (note && !notes.has(note.note_id)) {
        notes.set(note.note_id, note);
        added++;
      }
    }
    stats.scans++;
    return { visible: cards.length, added: added };
  }

  function flush(endpoint) {
    if (!endpoint) return Promise.resolve(null);
    return fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes: Array.from(notes.values()) })
    });
  }

  async function start(options) {
    if (running) return { error: 'already running' };
    var opts = options || {};
    var step = opts.scrollStep || 680;
    var idleRounds = opts.idleRounds || 6;
    var maxRounds = opts.maxRounds || 500;
    var delayMs = opts.delayMs || 250;
    var flushEvery = opts.flushEvery || 5;
    var endpoint = opts.endpoint || '';
    var scroller = opts.scrollSelector ? document.querySelector(opts.scrollSelector) : null;

    running = true;
    stats = { rounds: 0, idle: 0, lastAdded: 0, scans: 0 };
    scan();
    await flush(endpoint);

    while (stats.rounds < maxRounds && stats.idle < idleRounds) {
      if (scroller) scroller.scrollTop += step;
      else window.scrollBy(0, step);
      await new Promise(function (resolve) { setTimeout(resolve, delayMs); });

      var found = scan();
      stats.rounds++;
      stats.lastAdded = found.added;
      stats.idle = found.added === 0 ? stats.idle + 1 : 0;
      if (stats.rounds % flushEvery === 0) await flush(endpoint);
    }

    await flush(endpoint);
    running = false;
    return {
      total: notes.size,
      rounds: stats.rounds,
      idle: stats.idle,
      scans: stats.scans,
      running: false
    };
  }

  function stop() {
    running = false;
    return { total: notes.size };
  }

  function result() {
    return Array.from(notes.values());
  }

  window.__xhsBoardCollector = {
    start: start,
    stop: stop,
    result: result,
    parseCard: parseCard,
    selectors: SELECTORS,
    get running() { return running; },
    get stats() { return stats; }
  };
  return 'installed';
})();
