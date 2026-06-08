from __future__ import annotations

from aegis.models import Finding, RepoKnowledge

from .base import BaseAgent, evidence_from_records


RISK_IMPORTS = {
    "subprocess": "命令执行",
    "os": "系统调用",
    "pickle": "不安全反序列化",
    "eval": "动态执行",
    "exec": "动态执行",
    "jwt": "鉴权令牌",
    "crypto": "加密逻辑",
    "bcrypt": "密码处理",
}


class RiskAnalyst(BaseAgent):
    name = "RiskAnalyst"

    def analyze(self, knowledge: RepoKnowledge) -> list[Finding]:
        findings: list[Finding] = []
        risky = []
        for record in knowledge.files:
            tokens = _risk_tokens(record.imports + record.symbols + [record.path])
            if any(key in tokens for key in RISK_IMPORTS):
                risky.append(record)
        if risky:
            findings.append(
                self.finding(
                    "安全敏感实现候选",
                    f"发现 {len(risky)} 个涉及命令执行、鉴权、加密或动态执行的文件候选，需要人工复核。",
                    severity="high",
                    evidence=evidence_from_records(risky[:8]),
                    tags=["risk", "security"],
                )
            )
        complex_files = [record for record in knowledge.files if record.lines >= 600]
        if complex_files:
            findings.append(
                self.finding(
                    "复杂度风险",
                    f"发现 {len(complex_files)} 个超过 600 行的文件，维护风险较高。",
                    severity="medium",
                    evidence=evidence_from_records(complex_files[:5]),
                    tags=["risk", "complexity"],
                )
            )
        if not findings:
            findings.append(
                self.finding(
                    "未发现高风险静态线索",
                    "基于当前规则没有发现明显安全敏感或复杂度热点。该结论仅代表静态启发式扫描结果。",
                    confidence=0.6,
                    tags=["risk"],
                )
            )
        return findings


def _risk_tokens(values: list[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        normalized = value.lower().replace("\\", "/").replace("-", "_")
        for part in normalized.replace(".", "/").split("/"):
            stem = part.rsplit(".", 1)[0]
            tokens.add(stem)
            tokens.update(piece for piece in stem.split("_") if piece)
    return tokens
