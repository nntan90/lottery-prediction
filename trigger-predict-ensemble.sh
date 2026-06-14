#!/bin/bash
# Script để trigger workflow 02-predict-ensemble.yml manually

REPO="nntan90/lottery-prediction"
WORKFLOW_FILE="02-predict-ensemble.yml"
GITHUB_TOKEN="${GITHUB_TOKEN}"

if [ -z "$GITHUB_TOKEN" ]; then
    echo "❌ Error: GITHUB_TOKEN environment variable is not set"
    echo "Set it with: export GITHUB_TOKEN=your_github_token"
    exit 1
fi

echo "🚀 Triggering workflow: $WORKFLOW_FILE"
echo "📦 Repository: $REPO"
echo ""

curl -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW_FILE/dispatches" \
  -d '{"ref":"main"}' \
  -v

echo ""
echo "✅ Workflow dispatch request sent!"
echo "📊 Check status: https://github.com/$REPO/actions/workflows/$WORKFLOW_FILE"
