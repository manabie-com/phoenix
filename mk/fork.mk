#=============================================================================
# Fork maintenance
#=============================================================================
#
# Targets that exist only in this fork, kept out of the root Makefile on purpose.
#
# Upstream edits the root Makefile's shared `.PHONY` line and appends new sections
# just above `# Cleanup` — which is exactly where fork additions used to sit, so both
# collided on every sync. `.PHONY` accumulates across declarations, so declaring the
# fork's targets here means the fork no longer touches that shared line at all.
#
# The root Makefile carries a single `-include mk/fork.mk` at its end. This file is
# fork-owned, so nothing in it can ever conflict.
#
# Variables used here ($(UV), $(CYAN), $(GREEN), $(NC)) come from the root Makefile,
# which defines them well before the include.

.PHONY: sync-fork sync-fork-check install-gcs

install-gcs: ## Install the Google Cloud Storage SDK, for keeping prompt media in a bucket
	@echo -e "$(CYAN)Installing Google Cloud Storage support...$(NC)"
	@$(UV) pip install -r requirements/fork-gcs.txt
	@echo -e "$(GREEN)✓ Set PHOENIX_MEDIA_GCS_BUCKET to store prompt media in GCS$(NC)"

sync-fork-check: ## Report what syncing with upstream would conflict on (changes nothing)
	@$(UV) run python scripts/sync_fork.py --check

sync-fork: ## Merge upstream, regenerate generated files, re-point migrations
	@echo -e "$(CYAN)Syncing with upstream...$(NC)"
	@$(UV) run python scripts/sync_fork.py
	@echo -e "$(GREEN)✓ Merge staged — review, run tests, then commit$(NC)"
