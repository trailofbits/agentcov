# Proposed Homebrew formula for a trailofbits tap; see README.md next to this
# file. Not consumed by anything in this repository.
class Agentcov < Formula
  include Language::Python::Virtualenv

  desc "AI-agent read coverage for source files, with LCOV and gcov reports"
  homepage "https://github.com/trailofbits/agentcov"
  # The pure-Python wheel installs without needing the uv_build backend that
  # building the sdist would require.
  url "https://files.pythonhosted.org/packages/ff/b8/309931e461d0db6a97c07c923ce0f5a2d3e3665feaad0f10fd35bcfd1d33/agentcov-0.1.0-py3-none-any.whl"
  sha256 "bb1d2c2076e96d99073bd919f858d72d43ab477973cce16ff0881b0b11889a21"
  license "Apache-2.0"

  depends_on "python@3.13"

  def install
    venv = virtualenv_create(libexec, "python3.13")
    venv.pip_install_and_link cached_download
  end

  test do
    system bin/"agentcov", "--help"
    # summary works against any git repository, including an empty one.
    system "git", "init", "-q", testpath
    cd testpath do
      system bin/"agentcov", "summary"
    end
  end
end
