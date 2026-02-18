#!/bin/bash
# Blue-Green Deployment Helper for Azure App Service
# Supports automated slot management, health checks, and rollback

set -euo pipefail

# Configuration
RESOURCE_GROUP="${1:-llm-pricing-rg}"
APP_NAME="${2:-llm-pricing-api}"
STAGING_SLOT="staging"
HEALTH_CHECK_TIMEOUT=60
WAIT_BETWEEN_CHECKS=5

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[⚠]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    if ! command -v az &> /dev/null; then
        log_error "Azure CLI not found"
        exit 1
    fi
    
    if ! command -v curl &> /dev/null; then
        log_error "curl not found"
        exit 1
    fi
    
    log_success "Prerequisites met: Azure CLI, curl available"
}

# Get slot URLs
get_slot_url() {
    local slot=$1
    if [ "$slot" = "production" ]; then
        echo "https://${APP_NAME}.azurewebsites.net"
    else
        echo "https://${APP_NAME}-${slot}.azurewebsites.net"
    fi
}

# Health check for a slot
health_check_slot() {
    local slot=$1
    local url=$(get_slot_url "$slot")
    local elapsed=0
    
    log_info "Performing health check on $slot slot..."
    
    while [ $elapsed -lt $HEALTH_CHECK_TIMEOUT ]; do
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X GET "${url}/health" 2>/dev/null || echo "000")
        
        if [ "$HTTP_CODE" = "200" ]; then
            log_success "$slot slot is healthy (HTTP $HTTP_CODE)"
            return 0
        fi
        
        log_warning "$slot slot returned HTTP $HTTP_CODE, waiting... ($elapsed/${HEALTH_CHECK_TIMEOUT}s)"
        sleep $WAIT_BETWEEN_CHECKS
        elapsed=$((elapsed + WAIT_BETWEEN_CHECKS))
    done
    
    log_error "$slot slot failed health check after ${HEALTH_CHECK_TIMEOUT}s"
    return 1
}

# Functional test for a slot
functional_test_slot() {
    local slot=$1
    local url=$(get_slot_url "$slot")
    
    log_info "Running functional tests on $slot slot..."
    
    # Test /models endpoint
    MODELS_RESPONSE=$(curl -s "$url/models" 2>/dev/null || echo "{}")
    TOTAL_MODELS=$(echo "$MODELS_RESPONSE" | grep -o '"total_models":[0-9]*' | grep -o '[0-9]*' || echo "0")
    
    if [ "$TOTAL_MODELS" -gt 0 ]; then
        log_success "Models endpoint working: $TOTAL_MODELS models available"
    else
        log_error "Models endpoint returned invalid data"
        return 1
    fi
    
    # Test /pricing endpoint
    PRICING_RESPONSE=$(curl -s "$url/pricing" 2>/dev/null || echo "{}")
    if echo "$PRICING_RESPONSE" | grep -q '"models"'; then
        log_success "Pricing endpoint working: pricing data available"
    else
        log_error "Pricing endpoint failed"
        return 1
    fi
    
    # Test /telemetry endpoint
    TELEMETRY_RESPONSE=$(curl -s "$url/telemetry" 2>/dev/null || echo "{}")
    if echo "$TELEMETRY_RESPONSE" | grep -q '"overall_stats"'; then
        log_success "Telemetry endpoint working: analytics available"
    else
        log_warning "Telemetry endpoint may not be ready yet"
    fi
    
    return 0
}

# Swap slots (blue-green deployment)
swap_slots() {
    log_info "Swapping $STAGING_SLOT slot to production..."
    
    if ! az webapp deployment slot swap \
        --resource-group "$RESOURCE_GROUP" \
        --name "$APP_NAME" \
        --slot "$STAGING_SLOT"; then
        log_error "Failed to swap slots"
        return 1
    fi
    
    log_success "Slot swap completed"
    return 0
}

# Rollback to previous slot
rollback_slots() {
    log_warning "Rolling back: swapping slots back..."
    
    if ! az webapp deployment slot swap \
        --resource-group "$RESOURCE_GROUP" \
        --name "$APP_NAME" \
        --slot "$STAGING_SLOT"; then
        log_error "Failed to rollback slots"
        return 1
    fi
    
    log_success "Rollback completed"
    return 0
}

# Main deployment function
deploy() {
    log_info "Starting blue-green deployment..."
    
    # Wait for slot to be ready
    sleep 30
    
    # Health check staging
    if ! health_check_slot "$STAGING_SLOT"; then
        log_error "Staging slot health check failed, aborting deployment"
        return 1
    fi
    
    # Functional tests on staging
    if ! functional_test_slot "$STAGING_SLOT"; then
        log_error "Staging slot functional tests failed, aborting deployment"
        return 1
    fi
    
    # Swap to production
    if ! swap_slots; then
        log_error "Slot swap failed, attempting rollback..."
        rollback_slots
        return 1
    fi
    
    # Verify production
    if ! health_check_slot "production"; then
        log_warning "Production health check failed, but swap completed"
        log_warning "Monitor the application and consider rolling back if issues persist"
        return 0  # Don't fail the deployment script
    fi
    
    log_success "Blue-green deployment completed successfully!"
    return 0
}

# Get current slots status
status() {
    log_info "Deployment slots status:"
    
    az webapp deployment slot list \
        --resource-group "$RESOURCE_GROUP" \
        --name "$APP_NAME" \
        --output table
}

# Get deployment history
history() {
    log_info "Recent deployments:"
    
    az webapp deployment slot list \
        --resource-group "$RESOURCE_GROUP" \
        --name "$APP_NAME" \
        --output json | jq '.[] | {name, state, instanceStatuses}'
}

# Main
main() {
    local command="${1:-deploy}"
    
    case "$command" in
        check)
            check_prerequisites
            ;;
        deploy)
            check_prerequisites
            deploy
            ;;
        status)
            check_prerequisites
            status
            ;;
        history)
            check_prerequisites
            history
            ;;
        rollback)
            check_prerequisites
            rollback_slots
            ;;
        swap)
            check_prerequisites
            swap_slots
            ;;
        health)
            check_prerequisites
            health_check_slot "${2:-staging}"
            ;;
        test)
            check_prerequisites
            functional_test_slot "${2:-staging}"
            ;;
        *)
            echo "Usage: $0 {check|deploy|status|history|rollback|swap|health|test}"
            echo ""
            echo "Commands:"
            echo "  check              - Check prerequisites"
            echo "  deploy             - Full blue-green deployment workflow"
            echo "  status             - Show deployment slots status"
            echo "  history            - Show recent deployments"
            echo "  rollback           - Rollback to previous slot"
            echo "  swap               - Swap staging to production"
            echo "  health [slot]      - Run health check on slot (default: staging)"
            echo "  test [slot]        - Run functional tests on slot (default: staging)"
            exit 1
            ;;
    esac
}

main "$@"
