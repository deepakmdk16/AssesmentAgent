#!/usr/bin/env bash
# Install this repo's git hooks. Re-run after cloning (hooks are not versioned).
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
hook="$root/.git/hooks/pre-push"
printf '#!/usr/bin/env bash\nexec "$(git rev-parse --show-toplevel)/scripts/checkpoints.sh"\n' > "$hook"
chmod +x "$hook"
echo "installed pre-push -> scripts/checkpoints.sh"
