#!/usr/bin/env bash
# Create a new external-memory hub from the templates in this directory.
#
#   scaffold.sh <target-dir> "<Hub name>"
#
# Copies templates, substitutes the name, installs the lint and git hooks,
# sets core.hooksPath, runs the lint, and makes the first commit.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${1:?usage: scaffold.sh <target-dir> \"<Hub name>\"}"
name="${2:?usage: scaffold.sh <target-dir> \"<Hub name>\"}"

if [ -e "$target" ]; then
  echo "scaffold: $target already exists; refusing to overwrite" >&2
  exit 1
fi

mkdir -p "$target/scripts" "$target/hooks"
cp -R "$here/templates/." "$target/"
cp "$here/lint.py" "$target/scripts/lint.py"
cp "$here/hooks/pre-commit" "$here/hooks/commit-msg" "$target/hooks/"
chmod +x "$target/scripts/lint.py" "$target/hooks/pre-commit" "$target/hooks/commit-msg"

# Substitute the hub name in every template file.
find "$target" -type f \( -name '*.md' -o -name '*.txt' \) -print0 \
  | xargs -0 perl -pi -e "s/\\{\\{NAME\\}\\}/$name/g"

git -C "$target" init -q
git -C "$target" config core.hooksPath hooks
python3 "$target/scripts/lint.py"
git -C "$target" add -A
git -C "$target" commit -q --no-gpg-sign -m "Scaffold $name external memory"

echo "scaffold: $target ready. Fill INDEX.md, add a spoke per initiative, then:"
echo "  gh repo create <owner>/$(basename "$target") --private --source \"$target\" --push"
