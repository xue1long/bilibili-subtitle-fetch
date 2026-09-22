# 小红书笔记媒体下载设计

## 目标

给定一个小红书笔记 URL，自动识别视频笔记或图文笔记，下载媒体并生成不含签名参数的元数据 Markdown。

## 范围

- 第一版只处理单条笔记 URL，支持 `/explore/<note_id>` 和 `/board/<note_id>`。
- 通过 Playwright 页面读取标题、作者、简介、发布时间标签和实际媒体资源。
- 视频笔记保存一个视频文件；图文笔记保存笔记正文区域的全部大图并去重。
- 下载请求携带来源页 Referer；签名 CDN URL 只在内存中使用，不写入 Markdown、日志或状态文件。
- 默认使用项目独立 profile `.chrome-xiaohongshu`，支持 `XHS_SFETCH_CHROME_PROFILE` 覆盖。

## 输出

```text
10_raw/03_小红书/
└── <note_id>/
    ├── <note_id>.md
    ├── video.mp4
    └── images/01.webp
```

视频笔记只生成 `video.mp4`，图文笔记只生成 `images/`；两者都生成同名 Markdown 元数据文件。

## 元数据

必须写入 `platform`, `note_id`, `note_type`, `title`, `uploader`, `url` 和 `asset_count`；有值时写入 `published_label` 和 `description`。`url` 使用不带 `xsec_token` 的规范链接。

## 失败语义

- `NOTE_NOT_FOUND`：无法解析笔记 ID或页面未加载目标笔记。
- `ASSET_NOT_FOUND`：页面未发现视频或图文大图资源。
- `ASSET_DOWNLOAD_FAILED`：任一目标资源下载失败。
- `LOGIN_REQUIRED`：匿名页面不可读且可选登录 profile 也无法读取笔记。

下载失败不留下最终 Markdown 或不完整资源目录。

## 不做

- 不批量扫描收藏夹，不处理 CAPTCHA、付费限制、DRM 或风控绕过。
- 不保存 Cookie、Authorization header、xsec_token、签名 CDN URL 或完整页面响应。
- 不把小红书页面解析逻辑并入 B 站或抖音解析器。
