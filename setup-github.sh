#!/usr/bin/env bash
# One-time setup: put this Spindle folder under Git and publish it to your GitHub account.
#   ./setup-github.sh            creates a public repo called "spindle"
#   ./setup-github.sh --private  creates a private one instead
set -euo pipefail
cd "$(dirname "$0")"

REPO=spindle
VISIBILITY=--public
[[ "${1:-}" == "--private" ]] && VISIBILITY=--private

say()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*"; }

# 1. tools ---------------------------------------------------------------------------------------
missing=()
command -v git >/dev/null || missing+=(git)
command -v gh  >/dev/null || missing+=(gh)
if ((${#missing[@]})); then
  say "Installing ${missing[*]} (sudo may ask for your password)"
  if command -v dnf >/dev/null; then sudo dnf install -y "${missing[@]}"
  elif command -v apt-get >/dev/null; then sudo apt-get install -y "${missing[@]}"
  elif command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm "${missing[@]/gh/github-cli}"
  else warn "Please install: ${missing[*]}"; exit 1; fi
fi

# 2. sign in to GitHub (opens your browser once) -----------------------------------------------
if ! gh auth status >/dev/null 2>&1; then
  say "Signing in to GitHub - follow the prompts (choose HTTPS, and log in with your browser)"
  gh auth login --hostname github.com --git-protocol https --web
fi
gh auth setup-git >/dev/null 2>&1 || true          # lets plain 'git push' use the same login
LOGIN=$(gh api user --jq .login)
ID=$(gh api user --jq .id)
NAME=$(gh api user --jq '.name // empty')
say "Signed in as $LOGIN"

# 3. the local repository ------------------------------------------------------------------------
[[ -d .git ]] || git init -q -b main
# Commit identity for THIS repo only. GitHub's no-reply address keeps your real email private.
git config user.name  >/dev/null || git config user.name "${NAME:-$LOGIN}"
git config user.email >/dev/null || git config user.email "${ID}+${LOGIN}@users.noreply.github.com"

# point the README / project links at your account
sed -i "s#github.com/OWNER/#github.com/${LOGIN}/#g" README.md pyproject.toml

git add -A
if ! git rev-parse -q --verify HEAD >/dev/null; then
  git commit -q -F - <<'MSG'
Spindle 1.3.2: secure CD ripper for Linux

Secure ripping with AccurateRip verification, MusicBrainz/GnuDB tagging,
automatic cover art, many output formats, CD playback, and a Tag Files
tool for existing music.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01TA5QfeUJFscJn1DkikfSRx
MSG
  say "Created the first commit"
elif ! git diff --cached --quiet; then
  git commit -q -m "Point project links at ${LOGIN}"
fi

# 4. GitHub --------------------------------------------------------------------------------------
if gh repo view "${LOGIN}/${REPO}" >/dev/null 2>&1; then
  say "github.com/${LOGIN}/${REPO} already exists - pushing to it"
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/${LOGIN}/${REPO}.git"
  git push -u origin main
else
  say "Creating github.com/${LOGIN}/${REPO} (${VISIBILITY#--})"
  gh repo create "${REPO}" "${VISIBILITY}" --source=. --remote=origin --push \
    --description "All-in-one secure CD ripper for Linux: AccurateRip, MusicBrainz tagging, cover art, many formats"
fi
say "Done: https://github.com/${LOGIN}/${REPO}"
