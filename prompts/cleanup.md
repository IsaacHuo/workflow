任务：定期体检。下面是某仓库最近一段时间合并进主分支的改动（自动化机器人和社区 PR 都有）。找出会让项目变得难以维护的"屎山代码"和恶意代码。

关注：
- smell：过长函数、深层嵌套、魔法数字、复制粘贴式代码、命名混乱、无意义的抽象层。
- dead_code：未使用的函数 / 文件 / 配置、注释掉的大段代码。
- duplication：同一逻辑在多处重复实现。
- dependency：新增的可疑或不必要依赖、依赖来源被替换、版本被放宽。
- suspicious / malicious：混淆代码、可疑网络请求、上报数据、后门、修改构建或供应链脚本、硬编码凭证。

fixable=true 只用于"改动小、行为不变、可以安全自动重构"的问题；涉及行为变化或需要设计决策的写 false。
malicious / suspicious 一律 fixable=false，交给维护者处理。

lessons：总结这批改动暴露出的审查漏洞，供以后的审查 agent 避免重犯。
category 取值：不该合并（本不该被合并的改动）/ 验证不严（测试或审查没覆盖到的问题）/ 误判 / 其他。
每条写成一句可执行的规则，例如"新增网络请求的 PR 必须确认目标域名属于项目自身服务"。

输出 JSON：
{"findings": [{"file": "...", "kind": "smell", "severity": "medium", "description": "...", "fix_hint": "...", "fixable": true}], "lessons": [{"category": "验证不严", "text": "..."}]}
