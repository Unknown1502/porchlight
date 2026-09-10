#!/usr/bin/env bash
#
# Create an AgentCore Policy engine and load policies/cedar/*.cedar into it.
#
#   bash deploy/setup_policy.sh                 # dry run: validate, change nothing
#   bash deploy/setup_policy.sh --apply         # create the engine
#   GATEWAY_ARN=arn:... bash deploy/setup_policy.sh --apply   # + load the policies
#
# What this script knows, and how it knows it
# -------------------------------------------
# The operations, the name constraint and the Cedar shape below were checked
# against a live account (bedrock-agentcore-control, us-west-2) on 2026-09-10,
# not inferred from documentation. Three findings are baked in:
#
#   1. Engine names must match ^[A-Za-z][A-Za-z0-9_]*$ — a hyphen is rejected.
#      The obvious default, "porchlight-policy", fails.
#   2. A Cedar policy that constrains the action MUST scope the resource to a
#      specific `AgentCore::Gateway::"<arn>"`. Type-scoped (`resource is
#      AgentCore::Gateway`) is accepted only for policies that do not name an
#      action.
#   3. A blanket forbid over all actions on all gateways is accepted by the API
#      and then lands in CREATE_FAILED with "Overly Restrictive: Policy Engine
#      will deny every request". CreatePolicy returning 200 does NOT mean the
#      policy exists; the status has to be read back.
#
# Consequence, stated plainly: Porchlight's five rules cannot be loaded without
# an AgentCore Gateway to point them at. Without GATEWAY_ARN this script creates
# the engine and stops, and says so, rather than loading something weaker and
# letting the README imply the boundary is live.

set -euo pipefail

cd "$(dirname "$0")/.."

CEDAR_DIR="policies/cedar"
ENGINE_NAME="${AGENTCORE_POLICY_ENGINE_NAME:-porchlight_policy}"
REGION="${AWS_REGION:-us-west-2}"
GATEWAY_ARN="${GATEWAY_ARN:-}"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[92m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[93m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[91mstopped:\033[0m %s\n\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight --
say "Preflight"

command -v aws >/dev/null 2>&1 || die "the AWS CLI is not on PATH."
ok "aws cli $(aws --version 2>&1 | cut -d' ' -f1)"

aws sts get-caller-identity >/dev/null 2>&1 \
  || die "no usable AWS credentials. Run 'aws configure' or export AWS_PROFILE."
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
ok "account ${ACCOUNT}, region ${REGION}"

[ -d "$CEDAR_DIR" ] || die "$CEDAR_DIR does not exist. Run this from the repo root."

shopt -s nullglob
POLICIES=("$CEDAR_DIR"/*.cedar)
shopt -u nullglob
[ ${#POLICIES[@]} -gt 0 ] || die "no .cedar files in $CEDAR_DIR."

for p in "${POLICIES[@]}"; do
  [ -s "$p" ] || die "$p is empty. A zero-byte policy file silently permits."
  ok "$(basename "$p") ($(wc -c < "$p" | tr -d ' ') bytes)"
done

if ! printf '%s\n' "$ENGINE_NAME" | grep -qE '^[A-Za-z][A-Za-z0-9_]*$'; then
  die "engine name '${ENGINE_NAME}' is invalid. AgentCore requires ^[A-Za-z][A-Za-z0-9_]*$ (no hyphens)."
fi
ok "engine name '${ENGINE_NAME}' satisfies the service's name constraint"

# The Cedar sources and the in-process engine must already agree before anything
# is deployed — shipping a rule the tests never exercised is how a policy layer
# becomes decorative.
say "Parity check (Cedar ↔ in-process engine)"
if PYTHONPATH=src python -m pytest -q tests/test_policy_parity.py >/dev/null 2>&1; then
  ok "policy parity tests pass"
else
  die "tests/test_policy_parity.py fails. Fix the drift before deploying."
fi

# ------------------------------------------------------- capability probe ---
say "AgentCore Policy availability"

if ! aws bedrock-agentcore-control list-policy-engines --region "$REGION" >/dev/null 2>&1; then
  warn "this CLI/account cannot call bedrock-agentcore-control:ListPolicyEngines."
  warn "upgrade the CLI (pip install --upgrade awscli) or check IAM permissions."
  die  "cannot continue without the AgentCore control-plane API."
fi
ok "bedrock-agentcore-control reachable; ListPolicyEngines succeeds"

if [ "$APPLY" -eq 0 ]; then
  cat <<EOF

  Dry run. Nothing was created.

    Engine name : ${ENGINE_NAME}
    Region      : ${REGION}
    Policies    : ${#POLICIES[@]} file(s) from ${CEDAR_DIR}
    Gateway ARN : ${GATEWAY_ARN:-<not set>}

  Re-run with --apply to create the policy engine.
  Set GATEWAY_ARN as well to also load the Cedar policies against that gateway.

EOF
  exit 0
fi

# ------------------------------------------------------------- create engine --
say "Creating policy engine"

EXISTING="$(aws bedrock-agentcore-control list-policy-engines --region "$REGION" \
  --query "policyEngines[?name=='${ENGINE_NAME}'].policyEngineId | [0]" --output text 2>/dev/null || echo "None")"

if [ "$EXISTING" != "None" ] && [ -n "$EXISTING" ]; then
  ENGINE_ID="$EXISTING"
  ok "reusing existing engine ${ENGINE_ID}"
else
  ENGINE_ID="$(aws bedrock-agentcore-control create-policy-engine --region "$REGION" \
    --name "$ENGINE_NAME" \
    --description "Porchlight community incident agent — Cedar authorisation boundary" \
    --query policyEngineId --output text)" \
    || die "CreatePolicyEngine failed."
  ok "created engine ${ENGINE_ID}"
fi

for _ in $(seq 1 30); do
  STATUS="$(aws bedrock-agentcore-control get-policy-engine --region "$REGION" \
    --policy-engine-id "$ENGINE_ID" --query status --output text 2>/dev/null || echo UNKNOWN)"
  [ "$STATUS" = "ACTIVE" ] && break
  sleep 2
done
[ "$STATUS" = "ACTIVE" ] || die "engine ${ENGINE_ID} is ${STATUS}, not ACTIVE."
ok "engine status ACTIVE"

# ------------------------------------------------------------ load policies --
if [ -z "$GATEWAY_ARN" ]; then
  cat <<EOF

  Engine created, policies NOT loaded.

    AGENTCORE_POLICY_ENGINE_ID=${ENGINE_ID}

  Porchlight's rules all constrain an action, and AgentCore requires such a
  policy to name a specific gateway. There is no gateway to name yet, so loading
  them now would either fail validation or land in CREATE_FAILED as overly
  restrictive. Create an AgentCore Gateway, then re-run:

    GATEWAY_ARN=arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT}:gateway/<id> \\
      bash deploy/setup_policy.sh --apply

  Until then Porchlight enforces the same rules in-process and reports the
  backend as "local Cedar-equivalent shim" in the dashboard, next to the audit
  log. That label is what keeps the demo honest.

EOF
  exit 0
fi

say "Loading Cedar policies against ${GATEWAY_ARN}"

FAILED=0
for p in "${POLICIES[@]}"; do
  NAME="$(basename "$p" .cedar | tr -c 'A-Za-z0-9_' '_')"
  STATEMENT="$(sed "s|<GATEWAY_ARN>|${GATEWAY_ARN}|g" "$p")"
  DEF="$(STATEMENT="$STATEMENT" python -c 'import json,os;print(json.dumps({"cedar":{"statement":os.environ["STATEMENT"]}}))')"

  POLICY_ID="$(aws bedrock-agentcore-control create-policy --region "$REGION" \
    --policy-engine-id "$ENGINE_ID" --name "$NAME" \
    --enforcement-mode ACTIVE --validation-mode FAIL_ON_ANY_FINDINGS \
    --definition "$DEF" --query policyId --output text 2>&1)" || {
      warn "$NAME rejected: ${POLICY_ID}"; FAILED=1; continue; }

  # CreatePolicy returning an id is not the same as the policy existing. Read the
  # status back — "Overly Restrictive" surfaces only here.
  sleep 2
  PSTATUS="$(aws bedrock-agentcore-control get-policy --region "$REGION" \
    --policy-engine-id "$ENGINE_ID" --policy-id "$POLICY_ID" --query status --output text 2>/dev/null || echo UNKNOWN)"
  if [ "$PSTATUS" = "CREATE_FAILED" ]; then
    REASON="$(aws bedrock-agentcore-control get-policy --region "$REGION" \
      --policy-engine-id "$ENGINE_ID" --policy-id "$POLICY_ID" \
      --query 'statusReasons[0]' --output text 2>/dev/null || echo "no reason returned")"
    warn "$NAME -> CREATE_FAILED: ${REASON}"
    FAILED=1
  else
    ok "$NAME -> ${POLICY_ID} (${PSTATUS})"
  fi
done

if [ "$FAILED" -eq 1 ]; then
  die "at least one policy did not reach a created state. Nothing is enforced at the gateway; do not describe the deployment as policy-protected."
fi

cat <<EOF

  All ${#POLICIES[@]} policies loaded and active.

    AGENTCORE_POLICY_ENGINE_ID=${ENGINE_ID}

  Put that in .env. The dashboard will then report the backend as
  "AgentCore Policy (gateway)" instead of the local shim.

EOF
ok "done"
