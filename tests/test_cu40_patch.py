"""build_files/cu40/patch-gfx10.py: the 40 CU change lands next to the right code, or stops.

The fixture copies only the shape of the lines the change is anchored to in Linux 7.2's
gfx_v10_0.c (checked against OpenGamingCollective/linux v7.2.9-ogc3, the kernel of Bazzite
44 when this was written; the full file was also compiled with the change).

    python3 tests/test_cu40_patch.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHER = os.path.join(os.path.dirname(HERE), "build_files", "cu40", "patch-gfx10.py")

FIXTURE = """#include <linux/module.h>
#include <linux/pci.h>
#include "amdgpu.h"

static int gfx_v10_0_get_cu_info(struct amdgpu_device *adev,
				 struct amdgpu_cu_info *cu_info);

static void gfx_v10_0_select_se_sh(struct amdgpu_device *adev, u32 se_num,
				   u32 sh_num, u32 instance, int xcc_id)
{
}

static int gfx_v10_0_get_cu_info(struct amdgpu_device *adev,
				 struct amdgpu_cu_info *cu_info)
{
	int i, j, k, counter, active_cu_number = 0;

	amdgpu_gfx_parse_disable_cu(adev, disable_masks, 4, 2);

	mutex_lock(&adev->grbm_idx_mutex);
	for (i = 0; i < adev->gfx.config.max_shader_engines; i++) {
	}
	mutex_unlock(&adev->grbm_idx_mutex);
	return 0;
}

static void other(struct amdgpu_device *adev)
{
	mutex_lock(&adev->grbm_idx_mutex);
}
"""


def run(text):
    with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False) as f:
        f.write(text)
    r = subprocess.run([sys.executable, PATCHER, f.name], capture_output=True, text=True)
    out = open(f.name).read()
    os.unlink(f.name)
    return r.returncode, out, r.stderr


def main():
    fails = 0

    def check(name, cond, info=""):
        nonlocal fails
        print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"\n{info}"))
        fails += not cond

    rc, out, err = run(FIXTURE)
    check("applies to the 7.2 layout", rc == 0, err)
    param = out.find("static int bc250_cc_write_mode;")
    check("module option after <linux/pci.h>", out.find("#include <linux/pci.h>") < param < out.find('#include "amdgpu.h"'))
    body = out.find("static int gfx_v10_0_get_cu_info(struct amdgpu_device *adev,\n\t\t\t\t struct amdgpu_cu_info *cu_info)\n{")
    unlock = out.find("bc250-40cu-enable")
    check("unlock inside get_cu_info, after mutex_lock and before the CU loop",
          body < out.find("mutex_lock", body) < unlock < out.find("for (i = 0;", body) < out.find("static void other"))
    check("only gated by option and PCI id 0x13FE",
          "if (bc250_cc_write_mode > 0 && adev->pdev->device == BC250_PCI_DEVICE_ID)" in out
          and "#define BC250_PCI_DEVICE_ID 0x13FE" in out)
    check("inserted once", out.count("bc250-40cu-enable") == 1 and out.count("module_param(bc250_cc_write_mode") == 1)
    check("leaves the forward declaration and other functions alone", out.count("bc250") - FIXTURE.count("bc250") > 0
          and "static void other(struct amdgpu_device *adev)\n{\n\tmutex_lock(&adev->grbm_idx_mutex);\n}" in out)

    rc, _, err = run(out)
    check("refuses an already changed file", rc != 0 and "already" in err, err)
    rc, _, err = run(FIXTURE.replace("\tmutex_lock(&adev->grbm_idx_mutex);\n\tfor (i", "\tfor (i"))
    check("stops when mutex_lock moved", rc != 0, err)
    rc, _, err = run(FIXTURE.replace("u32 instance, int xcc_id)", "u32 instance)"))
    check("stops when select_se_sh changed signature", rc != 0, err)
    rc, _, err = run(FIXTURE.replace("#include <linux/pci.h>\n", ""))
    check("stops without its include anchor", rc != 0, err)
    print("\nall passed" if not fails else f"\n{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
