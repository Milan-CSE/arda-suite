.PHONY: test test-jarvis test-memory check-shell

test: test-jarvis test-memory

test-jarvis:
	cd apps/jarvis && python3 -m pytest -q

test-memory:
	cd services/memory && python3 -m pytest -q

check-shell:
	bash -n apps/jarvis/install.sh
	bash -n apps/jarvis/scripts/install_rpi_service.sh
	bash -n services/memory/scripts/install-pi.sh
