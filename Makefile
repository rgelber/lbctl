# Makefile for lbctl -- also used by the RPM spec's %install section
# (packaging/lbctl.spec), so this is the single source of truth for
# install paths. Keep it working with plain `make install` for anyone
# installing by hand (no RPM) too.

PREFIX ?= /usr/local
DESTDIR ?=

BINDIR      = $(DESTDIR)$(PREFIX)/bin
BASHCOMPDIR = $(DESTDIR)$(PREFIX)/share/bash-completion/completions
ZSHCOMPDIR  = $(DESTDIR)$(PREFIX)/share/zsh/site-functions
MANDIR      = $(DESTDIR)$(PREFIX)/share/man/man1

# Values mirror the spec file (packaging/lbctl.spec); keep them in sync.
NAME    = lbctl
VERSION = 0.1.0
TARBALL = $(NAME)-$(VERSION).tar.gz
RPMSPEC = packaging/lbctl.spec

INSTALL         = install
INSTALL_PROGRAM = $(INSTALL) -m 0755
INSTALL_DATA    = $(INSTALL) -m 0644
INSTALL_DIR     = $(INSTALL) -d

.PHONY: all install uninstall check clean tarball release

all:
	@echo "Nothing to build -- lbctl is a self-contained script."
	@echo "Run 'make install' (optionally PREFIX=/usr DESTDIR=... for packaging)."

install:
	$(INSTALL_DIR) $(BINDIR)
	$(INSTALL_PROGRAM) lbctl $(BINDIR)/lbctl
	$(INSTALL_DIR) $(BASHCOMPDIR)
	$(INSTALL_DATA) completions/lbctl.bash $(BASHCOMPDIR)/lbctl
	$(INSTALL_DIR) $(ZSHCOMPDIR)
	$(INSTALL_DATA) completions/_lbctl $(ZSHCOMPDIR)/_lbctl
	$(INSTALL_DIR) $(MANDIR)
	$(INSTALL_DATA) man/lbctl.1 $(MANDIR)/lbctl.1

uninstall:
	rm -f $(BINDIR)/lbctl
	rm -f $(BASHCOMPDIR)/lbctl
	rm -f $(ZSHCOMPDIR)/_lbctl
	rm -f $(MANDIR)/lbctl.1

check:
	python3 -m py_compile lbctl
	bash -n completions/lbctl.bash
	zsh -n completions/_lbctl
	python3 -m unittest discover -s tests
	python3 scripts/manpage.py --check

clean:
	rm -rf __pycache__

# Build the source tarball the RPM spec expects (Source0 =
# lbctl-0.1.0.tar.gz). Stage the tree under a versioned top-level directory so
# %setup -q in the spec can cd into it, and skip VCS/cache/dist noise.
tarball:
	rm -f $(TARBALL); _tmp=$$(mktemp -d); mkdir -p "$$_tmp/$(NAME)-$(VERSION)"; for entry in *; do case "$$entry" in .git|dist|$(TARBALL)|_stage) continue ;; esac; cp -a "$$entry" "$$_tmp/$(NAME)-$(VERSION)/"; done; find "$$_tmp/$(NAME)-$(VERSION)" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true; tar -czf $(TARBALL) -C "$$_tmp" $(NAME)-$(VERSION); rm -rf "$$_tmp"

# Build the source tarball and the RPM, then store both artifacts under
# dist/ so they live together and are easy to install or attach to a
# release. Uses the standard rpmbuild source/build tree under $HOME/rpmbuild.
# rpmbuild only exists on Linux, so this fails fast with guidance otherwise.
release: tarball
	@command -v rpmbuild >/dev/null 2>&1 || { \
	  echo "rpmbuild not found -- it is Linux-only (install rpm-build)," >&2; \
	  echo "or build inside a Linux container." >&2; exit 1; }
	@mkdir -p dist
	@cp $(TARBALL) dist/
	@mkdir -p $(HOME)/rpmbuild/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS}
	@cp $(TARBALL) $(HOME)/rpmbuild/SOURCES/
	@cp $(RPMSPEC) $(HOME)/rpmbuild/SPECS/
	@rpmbuild -bb $(HOME)/rpmbuild/SPECS/$(notdir $(RPMSPEC))
	@if ls $(HOME)/rpmbuild/RPMS/*/*.rpm >/dev/null 2>&1; then \
	  for rpm in $(HOME)/rpmbuild/RPMS/*/*.rpm; do cp "$rpm" dist/; done; \
	else echo "no RPM produced by rpmbuild" >&2; exit 1; fi
	@echo "Stored artifacts in dist/:" && ls -1 dist
