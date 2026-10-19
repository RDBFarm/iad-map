"""The installer: valid shell, and it downloads every file the Pi programs
need. A program importing a module the installer doesn't fetch works in
every test and fails on the Pi."""
import ast, os, re, subprocess, unittest

import fixture as F

INSTALL = os.path.join(F.PI, "install.sh")


def install_list():
    with open(INSTALL) as f:
        m = re.search(r"^for f in (.+); do$", f.read(), re.M)
    return set(m.group(1).split())


class Installer(unittest.TestCase):
    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", INSTALL], check=True)

    def test_every_local_import_and_data_file_is_downloaded(self):
        listed = install_list()
        local = {f[:-3] for f in os.listdir(F.PI) if f.endswith(".py")}
        for f in sorted(listed):
            self.assertTrue(os.path.exists(os.path.join(F.PI, f)), f"install.sh downloads {f}, which isn't in pi/")
        for f in sorted(os.listdir(F.PI)):
            if not f.endswith(".py"):
                continue
            with open(os.path.join(F.PI, f)) as fh:
                src = fh.read()
            for node in ast.walk(ast.parse(src)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                        [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
                for n in names:
                    if n in local:
                        self.assertIn(n + ".py", listed, f"{f} imports {n}; install.sh doesn't download it")
            for data in re.findall(r'"([a-z_]+\.json)"', src):
                if os.path.exists(os.path.join(F.PI, data)):
                    self.assertIn(data, listed, f"{f} reads {data}; install.sh doesn't download it")

    def test_pins_the_merged_version(self):
        with open(INSTALL) as f:
            src = f.read()
        self.assertIn("api.github.com/repos/$REPO/commits/main", src)
        self.assertIn("raw.githubusercontent.com/$REPO/$SHA/pi", src)


if __name__ == "__main__":
    unittest.main()
