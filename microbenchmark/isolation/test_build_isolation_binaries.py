"""Compile isolation firmware and verify the parameterized load probes."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent


class IsolationBuildTest(unittest.TestCase):
    def test_complete_build_includes_eight_loads_for_each_bank(self):
        with tempfile.TemporaryDirectory(prefix="isolation-build-test-") as temp:
            output = Path(temp) / "binaries"
            subprocess.run(
                ["bash", str(HERE / "build_isolation_binaries.sh"), str(output)],
                check=True,
            )
            for family, section in (
                ("same", ".scratch_x.load_data"),
                ("other", ".scratch_y.load_data"),
            ):
                obj = output / "rp2350-objects" / f"bench_iso_load_{family}_8.o"
                disassembly = subprocess.check_output(
                    ["arm-none-eabi-objdump", "-d", str(obj)], text=True
                )
                loads = re.findall(
                    r"\bldr(?:\.n)?\s+r2,\s*\[r1(?:,\s*#0)?\]", disassembly
                )
                self.assertEqual(len(loads), 8, family)
                sections = subprocess.check_output(
                    ["arm-none-eabi-objdump", "-h", str(obj)], text=True
                )
                self.assertIn(section, sections)
            for platform in ("gem5", "stm32-flash", "rp2350-objects"):
                self.assertTrue((output / platform / "sha256sums.txt").is_file())


if __name__ == "__main__":
    unittest.main()  
