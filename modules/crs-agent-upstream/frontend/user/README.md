# 电路图检索系统 - 前端

基于 React + Vite + Tailwind CSS 的现代化 Web 前端。

## 快速开始

### 1. 安装依赖

```bash
npm install
# 或
pnpm install
# 或
yarn install
```

### 2. 启动开发服务器

```bash
npm run dev
```

访问：http://localhost:5173

如果前端跑在服务器上、后端不在同一台机器的 `127.0.0.1:8000`，请先指定代理目标：

```bash
VITE_API_PROXY_TARGET=http://<后端主机>:8000 npm run dev
```

例如后端也在当前机器但监听 `0.0.0.0:8000`，通常仍可用：

```bash
VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
```

如果你是通过 Nginx/容器部署正式环境，优先使用 `frontend/user/nginx.conf` 中的 `/api` 反向代理，而不是直接跑 Vite 开发服务器。

### 3. 构建生产版本

```bash
npm run build
```

构建产物输出到 `dist/` 目录。

## 项目结构

```
frontend/
├── public/              # 静态资源
├── src/
│   ├── main.tsx         # 应用入口
│   ├── App.tsx          # 根组件
│   ├── components/      # React 组件（待实现）
│   ├── pages/           # 页面组件（待实现）
│   ├── hooks/           # 自定义 Hooks（待实现）
│   ├── services/        # API 调用
│   │   └── api.ts       # Axios 封装
│   ├── types/           # TypeScript 类型定义
│   │   └── index.ts     # API 响应类型
│   ├── utils/           # 工具函数（待实现）
│   └── styles/          # 全局样式
│       └── index.css    # Tailwind CSS
├── index.html           # HTML 模板
├── package.json         # 依赖配置
├── vite.config.ts       # Vite 配置
├── tsconfig.json        # TypeScript 配置
└── tailwind.config.js   # Tailwind 配置
```

## 开发计划

- [x] 基础页面布局（搜索框 + 结果区域）
- [ ] 搜索结果列表组件
- [ ] 文件预览组件（PDF/图片/文本）
- [ ] 澄清对话框组件
- [ ] 标签列表组件
- [ ] 加载状态与错误处理
- [ ] 响应式设计（移动端优化）
- [ ] 搜索历史记录
- [ ] 高亮显示匹配关键词

## 技术栈

- **框架**: React 18 + TypeScript
- **构建工具**: Vite 5
- **样式**: Tailwind CSS 3
- **状态管理**: React Query (服务端状态) + Zustand (客户端状态)
- **HTTP 客户端**: Axios
- **PDF 预览**: react-pdf
- **路由**: React Router (可选)

## 开发规范

### 组件命名
- 使用 PascalCase：`SearchBar.tsx`
- 一个文件一个组件

### 样式
- 优先使用 Tailwind CSS 工具类
- 避免内联样式
- 复杂样式使用 `@apply` 指令

### 类型定义
- 所有 API 响应定义类型
- 组件 Props 必须定义类型
- 避免使用 `any`

## 常用命令

```bash
# 开发
npm run dev

# 构建
npm run build

# 预览构建产物
npm run preview

# 代码检查
npm run lint
```

## 环境变量

在 `.env` 文件中配置（可选）：

```
VITE_API_BASE_URL=/api
VITE_API_PROXY_TARGET=http://127.0.0.1:8000
```
