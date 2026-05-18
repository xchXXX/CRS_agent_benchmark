# Migration Notes

旧项目能力接入新项目时遵循以下规则：

1. 不直接 import 旧项目运行时代码作为主链路依赖
2. 需要复用的业务能力，优先复制到新项目
3. 复制后通过 adapter 封装，再接入 Agent Runtime
4. AskUser、message history、Mem0 相关逻辑只在新项目中实现

