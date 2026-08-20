#!/usr/bin/env bash
set -euo pipefail

if (( $# != 1 )); then
  echo "usage: scripts/sync_public_mirror.sh <github-public-remote>" >&2
  exit 2
fi

remote=$1
root=$(git rev-parse --show-toplevel)
cd "$root"

if [[ "$(git branch --show-current)" != "main" ]] || \
   ! git diff --quiet || ! git diff --cached --quiet; then
  echo "public sync requires a clean main checkout" >&2
  exit 1
fi

remote_url=$(git remote get-url "$remote")
case "$remote_url" in
  https://github.com/*/*.git)
    repository=${remote_url#https://github.com/}
    repository=${repository%.git}
    ;;
  git@github.com:*/*.git)
    repository=${remote_url#git@github.com:}
    repository=${repository%.git}
    ;;
  *)
    echo "public mirror remote must be an explicit GitHub repository URL" >&2
    exit 1
    ;;
esac
if [[ "${repository##*/}" != *-public ]]; then
  echo "refusing to rewrite a repository whose name does not end in -public" >&2
  exit 1
fi
if [[ "$(gh api "repos/$repository" --jq .visibility)" != "public" ]]; then
  echo "target repository is not public" >&2
  exit 1
fi
if [[ "$(gh api "repos/$repository/actions/secrets" --jq .total_count)" != "0" ]] || \
   [[ "$(gh api "repos/$repository/actions/variables" --jq .total_count)" != "0" ]]; then
  echo "public mirror has repository Secrets or Variables; remove them before syncing" >&2
  exit 1
fi

gh api --method PUT "repos/$repository/actions/permissions" -F enabled=false >/dev/null
gh api --method PATCH "repos/$repository" \
  -f 'security_and_analysis[dependency_graph][status]=disabled' >/dev/null

temporary_root=$(mktemp -d /tmp/poly-edge-public-sync.XXXXXX)
cleanup() {
  gh api --method DELETE "repos/$repository/git/refs/tags/public-mirror-tree-staging" \
    >/dev/null 2>&1 || true
  find "$temporary_root" -depth -delete
}
trap cleanup EXIT

snapshot="$temporary_root/snapshot"
python3 scripts/build_public_mirror.py \
  --source "$root" --ref HEAD --destination "$snapshot"

git -C "$snapshot" init -q --initial-branch=main
git -C "$snapshot" add --all
tree_sha=$(git -C "$snapshot" write-tree)

# Publish only the tree object. The canonical parentless commit is created by
# GitHub so the private repository's local author identity is never copied.
git -C "$snapshot" push -q "$remote_url" \
  "$tree_sha:refs/tags/public-mirror-tree-staging"
commit_sha=$(
  gh api --method POST "repos/$repository/git/commits" \
    -f message='Publish identity-free source snapshot' \
    -f tree="$tree_sha" \
    --jq .sha
)
gh api --method PATCH "repos/$repository/git/refs/heads/main" \
  -f sha="$commit_sha" -F force=true >/dev/null
gh api --method DELETE "repos/$repository/git/refs/tags/public-mirror-tree-staging" \
  >/dev/null

test "$(gh api "repos/$repository/actions/permissions" --jq .enabled)" = "false"
test "$(gh api "repos/$repository/actions/secrets" --jq .total_count)" = "0"
test "$(gh api "repos/$repository/actions/variables" --jq .total_count)" = "0"
printf 'Published public snapshot %s to %s\n' "$commit_sha" "$repository"
