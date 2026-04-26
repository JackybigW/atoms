import re

def filter_content(content, has_draft_plan=True):
    content_to_emit = content or ""
    if content_to_emit and has_draft_plan:
        pattern = re.compile(r"```json\s*\{.*?\"request_key\".*?\"items\".*?\}\s*```", re.DOTALL)
        content_to_emit = pattern.sub("", content_to_emit).strip()
        c_stripped = content_to_emit.strip()
        if c_stripped.startswith("{") and c_stripped.endswith("}") and '"request_key"' in c_stripped and '"items"' in c_stripped:
            content_to_emit = ""
    return content_to_emit

cases = [
    # 1. Preamble + markdown JSON
    ("Here is the plan:\n```json\n{\n  \"request_key\": \"x\",\n  \"items\": [{\"id\": \"1\", \"text\": \"y\"}]\n}\n```", "Here is the plan:"),
    # 2. Just preamble
    ("I'll help you build that! Let me start by drafting a plan.", "I'll help you build that! Let me start by drafting a plan."),
    # 3. Just raw JSON object
    ("{\n  \"request_key\": \"x\",\n  \"items\": [{\"id\": \"1\", \"text\": \"y\"}]\n}", ""),
    # 4. Just markdown JSON
    ("```json\n{\n  \"request_key\": \"x\",\n  \"items\": [{\"id\": \"1\", \"text\": \"y\"}]\n}\n```", "")
]

for i, (inp, expected) in enumerate(cases):
    out = filter_content(inp)
    assert out == expected, f"Case {i+1} failed: {repr(out)} != {repr(expected)}"
print("All passed!")
