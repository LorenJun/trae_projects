p = "README_使用指南.md"
s = open(p, encoding="utf-8").read()
a = "- SoT 写回或 runtime-only 归档"
b = "- SoT 完整写回，或非 SoT 的 `archive_only` 归档"
c = "错误。欧战 / 杯赛默认是 runtime-only 路径，不直接写五大联赛 `teams_2025-26.md`。"
d = "错误。欧战 / 杯赛默认走 `archive_only` 路径（仅归档 + 赛果同步 + 准确率），不写五大联赛 `teams_2025-26.md`、不写 `MEMORY.md`、不进 RAG。"
print("a found", a in s, "| c found", c in s)
s = s.replace(a, b).replace(c, d)
open(p, "w", encoding="utf-8").write(s)
print("after runtime-only count =", s.count("runtime-only"))
