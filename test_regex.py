import re

content = """Here is the plan:
```json
{
  "request_key": "x",
  "items": [{"id": "1", "text": "y"}]
}
```
"""

pattern = re.compile(r"```json\s*\{.*\"request_key\".*\"items\".*\}\s*```", re.DOTALL)
new_content = pattern.sub("", content).strip()
print(repr(new_content))
