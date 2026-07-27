---
title: 'Run macOS wake-manager tests on Ubuntu CI'
type: 'bugfix'
created: '2026-07-27'
status: 'done'
route: 'one-shot'
---

# Run macOS wake-manager tests on Ubuntu CI

## Intent

**Problem:** Regression CI failed because five fake-`pmset` tests execute a `/bin/zsh` script while the Ubuntu runner did not provide zsh.

**Approach:** Install zsh in the regression job and include manager/workflow changes in its push path filters, preserving all wake-safety coverage instead of skipping it outside macOS.

## Suggested Review Order

- Install zsh before pytest so fake pmset/sudo tests remain cross-runner coverage.
  [`09-regression-tests.yml:28`](../../.github/workflows/09-regression-tests.yml#L28)

- Trigger regression when the local manager or this workflow changes.
  [`09-regression-tests.yml:6`](../../.github/workflows/09-regression-tests.yml#L6)
