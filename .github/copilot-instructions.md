# Python Code Review Priorities

When executing a code review (/review), evaluate the code strictly against these criteria:

- **Logical Bugs:** Check for type errors, mutable default arguments (`def standard(val=[])`), and improper error handling.
- **Performance:** Identify O(N^2) loops that could be O(N) using dictionaries/sets, and suggest generator expressions for large data.
- **PEP 8:** Enforce snake_case for variables/functions, PascalCase for classes, and require explicit return type hints.
- **Security:** Guard against `eval()`, insecure deserialization, SQL injection patterns, and hardcoded secrets.
