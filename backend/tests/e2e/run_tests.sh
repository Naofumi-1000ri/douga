#!/bin/bash
# run_tests.sh - Run the test_user E2E test agent

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"

cd "$BACKEND_DIR"

echo "=============================================="
echo "🧪 Douga E2E Test Runner"
echo "=============================================="
echo ""
echo "Backend dir: $BACKEND_DIR"
echo "Test dir: $SCRIPT_DIR"
echo ""

# Check if servers are running
echo "Checking services..."

if ! curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "❌ Backend not running on port 8000"
    echo "   Start with: cd $BACKEND_DIR && uvicorn src.main:app --reload"
    exit 1
fi
echo "✅ Backend is running"

if ! curl -s http://localhost:5173 > /dev/null 2>&1; then
    echo "❌ Frontend not running on port 5173"
    echo "   Start with: cd $BACKEND_DIR/../frontend && npm run dev"
    exit 1
fi
echo "✅ Frontend is running"

echo ""
echo "Starting tests..."
echo ""

# Run tests
python3 "$SCRIPT_DIR/test_user.py" "$@"

exit_code=$?

echo ""
echo "=============================================="
if [ $exit_code -eq 0 ]; then
    echo "✅ All tests passed!"
else
    echo "❌ Some tests failed (exit code: $exit_code)"
fi
echo "=============================================="

exit $exit_code
