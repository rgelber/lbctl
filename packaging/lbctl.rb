class Lbctl < Formula
  desc "Gracefully manage load balancer pool members for maintenance"
  homepage "https://github.com/rgelber/lbctl"
  url "https://github.com/rgelber/lbctl/archive/refs/tags/v0.2.0.tar.gz"
  version "0.2.0"
  license "MIT"

  # Self-contained, stdlib-only Python 3 script -- uses whatever `python3`
  # the shebang resolves to, so no Homebrew Python dependency is required.

  def install
    bin.install "lbctl"
    share.install "completions/lbctl.bash" => "bash-completion/completions/lbctl"
    share.install "completions/_lbctl" => "zsh/site-functions/_lbctl"
    share.install "man/lbctl.1" => "man/man1/lbctl.1"
  end

  test do
    assert_equal `#{bin}/lbctl --version`.strip, "lbctl 0.2.0"
    assert_path_exist share.join("man/man1/lbctl.1")
  end
end