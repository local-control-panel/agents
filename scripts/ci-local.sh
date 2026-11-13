#!/usr/bin/env bash
# Local CI: runs the checks of the hosted workflow on this machine and reports
# the result as the commit status `check` on HEAD, which is the context branch
# protection requires. The hosted workflow (.github/workflows/ci.yml) stays
# available as a manual run.
#
# Usage: scripts/ci-local.sh [--no-report]
# Needs: a clean tree (the result belongs to the commit, not the working copy),
# a pushed HEAD and an authenticated `gh` for reporting, Python 3.11+ (tomllib),
# shellcheck, and a Rust toolchain unless OPS_ENGINE points at an engine built
# from the commit pinned in .github/actions/build-engine/action.yml.
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

report=1
[[ "${1:-}" == "--no-report" ]] && report=0

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ci-local: the working tree is not clean; commit or stash first" >&2
  exit 2
fi

sha="$(git rev-parse HEAD)"
repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)"
if [[ $report -eq 1 && -z "$repo" ]]; then
  echo "ci-local: cannot resolve the GitHub repository (gh auth status)" >&2
  exit 2
fi

post() { # state description
  [[ $report -eq 1 ]] || return 0
  gh api "repos/$repo/statuses/$sha" -f state="$1" -f context="check" \
    -f description="$2" >/dev/null
}

python=""
for candidate in python3.15 python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null &&
    "$candidate" -c 'import tomllib' 2>/dev/null; then
    python="$candidate"
    break
  fi
done
if [[ -z "$python" ]]; then
  echo "ci-local: needs Python 3.11+ (tomllib)" >&2
  exit 2
fi
if ! command -v shellcheck >/dev/null; then
  echo "ci-local: shellcheck is not installed" >&2
  exit 2
fi

build_engine() {
  if [[ -n "${OPS_ENGINE:-}" && -x "$OPS_ENGINE" ]]; then
    return 0
  fi
  local pin cache
  pin="$(sed -n 's/^ *ref: \([0-9a-f]\{40\}\).*/\1/p' .github/actions/build-engine/action.yml)"
  if [[ -z "$pin" ]]; then
    echo "ci-local: no engine commit found in .github/actions/build-engine/action.yml" >&2
    return 1
  fi
  cache="${XDG_CACHE_HOME:-$HOME/.cache}/agents-ci-local/operations-engine"
  if [[ ! -d "$cache/.git" ]]; then
    mkdir -p "$cache" &&
      git clone --quiet https://github.com/orbden/operations-engine.git "$cache" || return 1
  fi
  git -C "$cache" cat-file -e "$pin^{commit}" 2>/dev/null ||
    git -C "$cache" fetch --quiet origin || return 1
  git -C "$cache" checkout --quiet --detach "$pin" || return 1
  (cd "$cache" && cargo build --release --locked) || return 1
  export OPS_ENGINE="$cache/target/release/ops-engine"
}

shellcheck_agents() {
  # find, not globstar: the bash that ships with macOS has no globstar.
  local files=()
  while IFS= read -r file; do files+=("$file"); done < <(find agents -name '*.sh' | sort)
  if [[ ${#files[@]} -eq 0 ]]; then return 0; fi
  echo "ci-local: ShellCheck on ${#files[@]} scripts"
  shellcheck "${files[@]}"
}

registry_builds() {
  local out
  out="$(mktemp)" || return 1
  # build_registry.py writes registry.json in the repository; restore it after.
  "$python" scripts/build_registry.py ci "$sha" &&
    "$python" -m json.tool registry.json >"$out"
  local status=$?
  rm -f "$out"
  git checkout -- registry.json 2>/dev/null || rm -f registry.json
  return $status
}

post pending "running locally"
failed=0
step() { # name command...
  local name="$1"
  shift
  if "$@"; then
    echo "ci-local: $name passed"
  else
    echo "ci-local: $name FAILED" >&2
    failed=1
  fi
}

step "engine build" build_engine
if [[ $failed -eq 0 ]]; then
  step "agent metadata and scripts" "$python" scripts/check_agents.py
  step "unit tests" "$python" -m unittest discover tests
  step "ShellCheck" shellcheck_agents
  step "registry builds" registry_builds
fi

if [[ $failed -eq 0 ]]; then
  post success "passed locally on $(date -u +%Y-%m-%dT%H:%MZ)"
  echo "ci-local: check passed"
else
  post failure "failed locally"
  echo "ci-local: check FAILED" >&2
fi
exit $failed
