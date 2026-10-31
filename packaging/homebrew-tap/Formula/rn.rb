class Rn < Formula
  include Language::Python::Virtualenv

  desc "Keyboard-driven terminal UI for batch renaming"
  homepage "https://github.com/Gradipoo/tui-renamer"
  url "https://files.pythonhosted.org/packages/f2/70/ba2a8eb8e307b2197b8d81afed61736ec62ec571946a7857d50bee939680/tui_renamer-0.1.3.tar.gz"
  sha256 "d2f0f89b3445642c47cadebc34029bb872706115207dd7895a2136954d4936ce"
  license "GPL-3.0-only"

  depends_on "cmake" => :build
  depends_on "pkgconf" => :build
  depends_on "jpeg-turbo"
  depends_on "libheif"
  depends_on "libtiff"
  depends_on "little-cms2"
  depends_on "python@3.12"
  depends_on "webp"

  # BEGIN resources
  resource "linkify-it-py" do
    url "https://files.pythonhosted.org/packages/45/98/7a1a5f31fd5c7ba93e963b168e244b8e3dd705b3d2a718e3c3307583bf57/linkify_it_py-2.2.0.tar.gz"
    sha256 "907acd2d17ac1fbb9ddb62c8957ccbd6158cac602231a15c3b0cd1e215f03cee"
  end

  resource "markdown-it-py" do
    url "https://files.pythonhosted.org/packages/06/ff/7841249c247aa650a76b9ee4bbaeae59370dc8bfd2f6c01f3630c35eb134/markdown_it_py-4.2.0.tar.gz"
    sha256 "04a21681d6fbb623de53f6f364d352309d4094dd4194040a10fd51833e418d49"
  end

  resource "mdit-py-plugins" do
    url "https://files.pythonhosted.org/packages/59/fc/f8d0863f8862f25602c0404d75568e89fb6b4109804645e5cdfb1be5cf56/mdit_py_plugins-0.6.1.tar.gz"
    sha256 "a2bca0f039f39dbd35fb74ae1b5f998608c437463371f0ff7f49a19a17a114d0"
  end

  resource "mdurl" do
    url "https://files.pythonhosted.org/packages/d6/54/cfe61301667036ec958cb99bd3efefba235e65cdeb9c84d24a8293ba1d90/mdurl-0.1.2.tar.gz"
    sha256 "bb413d29f5eea38f31dd4754dd7377d4465116fb207585f97bf925588687c1ba"
  end

  resource "piexif" do
    url "https://files.pythonhosted.org/packages/fa/84/a3f25cec7d0922bf60be8000c9739d28d24b6896717f44cc4cfb843b1487/piexif-1.1.3.zip"
    sha256 "83cb35c606bf3a1ea1a8f0a25cb42cf17e24353fd82e87ae3884e74a302a5f1b"
  end

  resource "pillow" do
    url "https://files.pythonhosted.org/packages/ce/a4/9cc30690a394a8305e8be4c9036aa16d0dfddadd09079860b582f562496d/pillow-13.0.0.tar.gz"
    sha256 "cdcd1ba258178f7356d74170c9a81ba4eab6a5d55748bc402a5a2a6fe6be3510"
  end

  resource "pillow-heif" do
    url "https://files.pythonhosted.org/packages/44/c1/82145984920ca055675af2c2795bd30da6f7461215c41f3c1eacb3d66353/pillow_heif-1.8.1.tar.gz"
    sha256 "521ebffb8a181d56c3904e5a61f20903edee0d9d3275967b8fb345f866215c06"
  end

  resource "platformdirs" do
    url "https://files.pythonhosted.org/packages/6e/4e/2684728d9f2c8c4110738dcadca7e0bed4b60c8dc68c40c2a9e0cf017900/platformdirs-4.14.2.tar.gz"
    sha256 "6d13b4c1d66b7548dcec9e3db13c1ba9d9adb65041b5d07869313afc07e6e41b"
  end

  resource "pygments" do
    url "https://files.pythonhosted.org/packages/49/2e/ced460408999b33da6b31b0021b0f37d329e202d4169aeb164493778f25b/pygments-2.21.0.tar.gz"
    sha256 "610ca751c9bc2492b38eb9a38a7fbc93edbbb2d7182edaf34e66ae493dee5c8c"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/c0/8f/0722ca900cc807c13a6a0c696dacf35430f72e0ec571c4275d2371fca3e9/rich-15.0.0.tar.gz"
    sha256 "edd07a4824c6b40189fb7ac9bc4c52536e9780fbbfbddf6f1e2502c31b068c36"
  end

  resource "textual" do
    url "https://files.pythonhosted.org/packages/00/21/39a76b01bd5eea82a04baaca7580e105d8c59450df03998345bb2cfb307b/textual-8.2.8.tar.gz"
    sha256 "3f106a9fbc73e39dd266c9712432087de78a6d644084c7c241d6a25c3169115b"
  end

  resource "textual-image" do
    url "https://files.pythonhosted.org/packages/e8/32/7844d7c06cfc540caeb3ad8e7f96e66a12daac8934f4d45a86cab879e7e1/textual_image-0.14.2.tar.gz"
    sha256 "0ec2ac1e2a9b30bfc478708ea011eb31e7844c377322ed2094976c1a84c131ba"
  end

  resource "typing-extensions" do
    url "https://files.pythonhosted.org/packages/f6/cc/6253133b5bb138fc3306cebfbda2c520f545d36b5be2c7255cc528bb45d6/typing_extensions-4.16.0.tar.gz"
    sha256 "dc983d19a509c94dba722ee6abd33940f7c05a89e243c47e907eb4db6f1a43e5"
  end

  resource "tzdata" do
    url "https://files.pythonhosted.org/packages/7a/29/6a1391d31cd00ff9e927215a224a880e1e84e826b64ba80a499e51c074d8/tzdata-2026.6.tar.gz"
    sha256 "85d2cd9c09f81f0bb29db27759fc83af4f1e620efb814f8c5bf8b4cc4feece6d"
  end

  # END resources

  def install
    venv = virtualenv_create(libexec, "python3.12", system_site_packages: true, without_pip: true)
    venv.pip_install resources
    venv.pip_install_and_link "#{buildpath}[heif]"
  end

  test do
    assert_match "needs a terminal", shell_output("#{bin}/rn 2>&1", 1)
  end
end
