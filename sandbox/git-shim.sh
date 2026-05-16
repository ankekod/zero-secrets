#!/bin/bash
# Network-touching git subcommands are intercepted here — the sandbox has no
# egress to github.com, so they'd time out anyway. Local git passes through
# to /usr/bin/git. This wrapper sits earlier on PATH (/usr/local/bin) so it
# wins for `git` invocations from any shell.
case "$1" in
    clone)
        cat >&2 <<EOF
git clone: this sandbox has no network egress to github.com, so the git
protocol cannot reach the remote from here.

Use 'sandbox-clone' instead — it routes the fetch through the control
plane (which holds the GitHub PAT), unpacks the tarball into /workspace,
and runs 'git init' so local git operations work on the snapshot:

  sandbox-clone                       # uses \$GITHUB_REPO / \$GITHUB_BRANCH
  sandbox-clone owner/repo
  sandbox-clone owner/repo some-branch

See /workspace/AGENTS.md for the full picture.
EOF
        exit 127
        ;;
    push|pull|fetch|ls-remote)
        cat >&2 <<EOF
git $1: this sandbox has no network egress to github.com, so remote git
operations cannot work here.

Use the github MCP server for anything that talks to GitHub:
  push_files / create_or_update_file   commit + push (one or many files)
  create_branch, list_branches         branch management
  create_pull_request, get_pull_request, list_pull_requests
  create_issue, list_issues, add_issue_comment
  get_file_contents                    fetch a file from the repo

To re-fetch a fresh snapshot of the whole repo, use 'sandbox-clone'.

See /workspace/AGENTS.md for details.

Local git works as normal: log, diff, status, branch, checkout, commit,
merge, rebase, blame, etc.
EOF
        exit 127
        ;;
    *)
        exec /usr/bin/git "$@"
        ;;
esac
