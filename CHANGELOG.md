# Changelog

Every merged pull request adds a line under **Unreleased**. A release moves
those lines under a version heading and tags that commit. Versions follow
[Semantic Versioning](https://semver.org/) and stay below 1.0 while the deck
is being built: a minor bump for a new user-visible capability, a patch bump
for fixes, dependencies and internal work. See "Versioning and releases" in
`AGENTS.md`.

## Unreleased

### Added
- `cardak` opens a live view of every WezTerm pane grouped by project, with the CPU and memory of everything running in each pane, including builds an agent started. Repos you haven't configured are grouped by their git root, and `a` adds one to your projects. (#2)
