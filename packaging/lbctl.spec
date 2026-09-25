Name:           lbctl
Version:        0.1.0
Release:        1%{?dist}
Summary:        Gracefully manage load balancer pool members for maintenance

License:        MIT
URL:            https://github.com/rgelber/lbctl
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

Requires:       python3 >= 3.8

%description
lbctl is a self-contained command-line tool for gracefully removing, disabling,
draining, and re-enabling members of a load balancer pool. It starts with the
F5 BIG-IP iControl REST API and is being built to support all load balancer
systems, using only the Python standard library -- no external Python
packages required.

Useful for maintenance: drain a member (disable + wait for active connections to
reach zero) or flip it back on. Ships with bash and zsh tab completions for
--partition/--pool/--member values.

%prep
%setup -q

%build
# Nothing to compile: lbctl is a single self-contained script.

%install
# Install from the Makefile -- the single source of truth for install paths.
# Nothing to build first.
make install DESTDIR=%{buildroot} PREFIX=%{_prefix}
# Remove any bytecode the Makefile's py_compile check might have produced.
find %{buildroot} -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

%files
%{_bindir}/lbctl
%{_datadir}/bash-completion/completions/lbctl
%{_datadir}/zsh/site-functions/_lbctl

%changelog
* Fri Sep 25 2026 - Ryan Gelber <ryangelber@gmail.com> - 0.1.0
- Initial package.