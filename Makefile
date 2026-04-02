PREFIX ?= $(HOME)/.local
BIN_DIR := $(PREFIX)/bin

.PHONY: test doctor install

test:
	./bin/asm self-test

doctor:
	./bin/asm doctor

install:
	mkdir -p "$(BIN_DIR)"
	ln -sf "$(PWD)/bin/asm" "$(BIN_DIR)/asm"
