Review the pull request for:

- **Code cleanliness**: Is the implementation clean and well-structured?
- **DRY principle**: Does it avoid duplication?
- **Code reuse**: Are there parts that should be reused from other places?
- **Organization**: Is everything in the right place?
- **Consistency**: Is it in the same style as other parts of the codebase?
- **Simplicity**: Is it not over-engineered? Remember KISS and YAGNI. No dead code paths and NO defensive programming.
- **User experience**: Does it provide a good user experience?
- **PR**: Is the PR description and title clear and informative?
- **Tests**: Are there tests, and do they cover the changes adequately? Are they testing something meaningful or are they just trivial?
- **Live tests**: Test the changes in a REAL live environment to ensure they work as expected, use the config in `/opt/stacks/compose-farm.yaml`.
- **Rules**: Does the code follow the project's coding standards and guidelines as laid out in @CLAUDE.md?

Look at `git diff origin/main..HEAD` for the changes made in this pull request.
