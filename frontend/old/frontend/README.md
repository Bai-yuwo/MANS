# MANS Frontend

前端资源目录，包含所有 Web 界面相关文件。

## 目录结构

```
frontend/
├── templates/              # HTML 模板
│   └── index.html         # 主页面模板
├── static/                # 静态资源
│   ├── css/               # 样式文件
│   │   └── style.css     # 主样式
│   ├── js/                # JavaScript
│   │   └── app.js        # 前端逻辑
│   └── assets/            # 其他资源 (图片、字体等)
└── README.md              # 本文件
```

## 文件说明

### templates/index.html
- 主页面模板
- 包含所有页面结构 (概览/项目/创作/监控/日志/设置)
- 使用 Flask Jinja2 模板引擎

### static/css/style.css
- 全局样式文件
- 暗色主题设计
- 响应式布局
- 动画效果

### static/js/app.js
- 前端核心逻辑
- API 调用封装
- 页面交互处理
- SSE 事件流接收

## 与后端关联

后端配置文件在 `web_app.py`:

```python
app = Flask(
    __name__,
    template_folder='frontend/templates',
    static_folder='frontend/static'
)
```

## 开发指南

1. **修改页面结构**: 编辑 `templates/index.html`
2. **修改样式**: 编辑 `static/css/style.css`
3. **修改逻辑**: 编辑 `static/js/app.js`
4. **添加资源**: 放入 `static/assets/` 目录

## API 调用

前端通过 `fetch()` 调用后端 API:

```javascript
// 示例: 获取项目列表
const response = await fetch('/api/projects');
const data = await response.json();
```

所有 API 端点定义在 `web_app.py` 中。
