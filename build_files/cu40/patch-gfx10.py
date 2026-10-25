#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
#
# STONE: add the BC-250 40 CU unlock to amdgpu's gfx_v10_0.c (Linux kernel source, GPL-2.0).
#
# The two inserted blocks are the ones of duggasco's patch "drm/amdgpu/gfx10: BC-250 40 CU
# unlock via CC + SPI register writes" (https://github.com/duggasco/bc250-40cu-unlock),
# unchanged. They are placed by looking for the code they go next to, not by line number,
# so a kernel that only moved lines around still gets them in the right place; if any anchor
# is missing or found twice the script stops and nothing is built.
#
# What the code does, only when the module option amdgpu.bc250_cc_write_mode is > 0 and only
# on PCI device 0x13FE (the BC-250 GPU): while amdgpu counts the GPU's compute units it clears
# the harvest mask (CC_GC_SHADER_ARRAY_CONFIG), lets the shader dispatcher use all 5 WGPs of
# each shader array (SPI_PG_ENABLE_STATIC_WGP_MASK = 0x1f) and keeps them powered
# (RLC_PG_ALWAYS_ON_WGP_MASK = 0x1f). With the option at 0 (default) nothing changes.
#
#   patch-gfx10.py <path to drivers/gpu/drm/amd/amdgpu/gfx_v10_0.c>
import re
import sys

PARAM = r'''
/* BC-250 40 CU unlock: clears harvest mask + enables SPI dispatch to all WGPs */
static int bc250_cc_write_mode;
module_param(bc250_cc_write_mode, int, 0444);
MODULE_PARM_DESC(bc250_cc_write_mode,
	"BC-250: 0=off 1=probe-SE0SH0 2=clear-SE0SH0 3=clear-all-SAs 4=probe-all-SAs");
#define BC250_PCI_DEVICE_ID 0x13FE
'''

UNLOCK = r'''
	/* BC-250: unlock harvested CUs -- CC (enumeration) + SPI (dispatch) + RLC (power) */
	if (bc250_cc_write_mode > 0 && adev->pdev->device == BC250_PCI_DEVICE_ID) {
		int bc_se, bc_sh;
		for (bc_se = 0; bc_se < adev->gfx.config.max_shader_engines; bc_se++) {
			for (bc_sh = 0; bc_sh < adev->gfx.config.max_sh_per_se; bc_sh++) {
				u32 bc_cc_orig, bc_cc_after, bc_spi_orig, bc_spi_after;
				if (bc250_cc_write_mode == 2 && (bc_se > 0 || bc_sh > 0))
					continue;
				gfx_v10_0_select_se_sh(adev, bc_se, bc_sh, 0xffffffff, 0);
				bc_cc_orig = RREG32_SOC15(GC, 0, mmCC_GC_SHADER_ARRAY_CONFIG);
				WREG32_SOC15(GC, 0, mmCC_GC_SHADER_ARRAY_CONFIG, 0);
				bc_cc_after = RREG32_SOC15(GC, 0, mmCC_GC_SHADER_ARRAY_CONFIG);
				bc_spi_orig = RREG32_SOC15(GC, 0, mmSPI_PG_ENABLE_STATIC_WGP_MASK);
				WREG32_SOC15(GC, 0, mmSPI_PG_ENABLE_STATIC_WGP_MASK, 0x1f);
				bc_spi_after = RREG32_SOC15(GC, 0, mmSPI_PG_ENABLE_STATIC_WGP_MASK);
				WREG32_SOC15(GC, 0, mmRLC_PG_ALWAYS_ON_WGP_MASK, 0x1f);
				if (bc250_cc_write_mode == 1 || bc250_cc_write_mode == 4) {
					WREG32_SOC15(GC, 0, mmCC_GC_SHADER_ARRAY_CONFIG, bc_cc_orig);
					WREG32_SOC15(GC, 0, mmSPI_PG_ENABLE_STATIC_WGP_MASK, bc_spi_orig);
					dev_info(adev->dev,
						"bc250-40cu-probe: se=%d sh=%d CC=0x%08x->0x%08x SPI=0x%08x->0x%08x (restored)",
						bc_se, bc_sh, bc_cc_orig, bc_cc_after, bc_spi_orig, bc_spi_after);
				} else {
					dev_info(adev->dev,
						"bc250-40cu-enable: mode=%d se=%d sh=%d CC=0x%08x->0x%08x SPI=0x%08x->0x%08x",
						bc250_cc_write_mode, bc_se, bc_sh,
						bc_cc_orig, bc_cc_after, bc_spi_orig, bc_spi_after);
				}
			}
		}
		gfx_v10_0_select_se_sh(adev, 0xffffffff, 0xffffffff, 0xffffffff, 0);
	}
'''


def fail(msg):
    print(f"patch-gfx10: {msg}", file=sys.stderr)
    sys.exit(1)


def once(pattern, text, what):
    found = list(re.finditer(pattern, text, re.M))
    if len(found) != 1:
        fail(f"{what}: expected 1 match, found {len(found)}")
    return found[0]


def patch(src):
    if "bc250_cc_write_mode" in src:
        fail("source already has bc250_cc_write_mode")
    # The calls in UNLOCK need these exact helpers and registers; stop if the kernel changed them.
    once(r"^static void gfx_v10_0_select_se_sh\(struct amdgpu_device \*adev, u32 se_num,\s*\n\s*u32 sh_num, u32 instance,\s*int xcc_id\)\s*\n\{",
         src, "gfx_v10_0_select_se_sh(adev, se, sh, instance, xcc_id) definition")
    inc = once(r"^#include <linux/pci\.h>\n", src, "#include <linux/pci.h>")
    src = src[:inc.end()] + PARAM + src[inc.end():]
    body = once(r"^static int gfx_v10_0_get_cu_info\(struct amdgpu_device \*adev,\s*\n\s*struct amdgpu_cu_info \*cu_info\)\s*\n\{\n",
                src, "gfx_v10_0_get_cu_info() definition")
    end = src.find("\n}\n", body.end())
    lock = src.find("\tmutex_lock(&adev->grbm_idx_mutex);\n", body.end(), end)
    if lock < 0:
        fail("mutex_lock(&adev->grbm_idx_mutex) not found inside gfx_v10_0_get_cu_info()")
    at = lock + len("\tmutex_lock(&adev->grbm_idx_mutex);\n")
    if not src[at:end].lstrip().startswith("for (i = 0; i < adev->gfx.config.max_shader_engines; i++)"):
        fail("the CU counting loop no longer follows mutex_lock in gfx_v10_0_get_cu_info()")
    return src[:at] + UNLOCK + src[at:]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        fail("usage: patch-gfx10.py <gfx_v10_0.c>")
    path = sys.argv[1]
    with open(path) as f:
        out = patch(f.read())
    with open(path, "w") as f:
        f.write(out)
    print(f"patch-gfx10: {path} patched")
