PREFIX ?= $(HOME)/.local
BIN_DIR := $(PREFIX)/bin

.PHONY: smoke test doctor install uninstall package

smoke:
	./bin/asm smoke-test

test:
	./bin/asm self-test

doctor:
	./bin/asm doctor

install:
	PREFIX="$(PREFIX)" ./scripts/install.sh

uninstall:
	rm -f "$(BIN_DIR)/asm"
	rm -rf "$(PREFIX)/libexec/asm-cli" "$(PREFIX)/share/doc/asm-cli"

package:
	./scripts/build-release.sh
