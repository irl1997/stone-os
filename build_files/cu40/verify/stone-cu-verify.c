/*
 * STONE: GPU compute correctness test, used by `stone-cu40 test` before 40 CU is kept.
 *
 *   stone-cu-verify <shader.spv> [--seconds S] [--elements N] [--iters I]
 *
 * Runs stone-cu-verify.comp over N elements again and again for S seconds and compares every
 * result with the same computation done on the CPU. Prints one line per pass and a summary:
 *   device=<name>
 *   pass=<k> seconds=<gpu time> errors=<count>
 *   summary passes=<k> checked=<values> errors=<count>
 * Exit: 0 all results right, 2 wrong results, 1 the test could not run.
 * Only a GPU is used; a CPU (software) Vulkan device is refused unless
 * STONE_CU_VERIFY_ALLOW_CPU=1 (tests of this program only).
 */
#define _POSIX_C_SOURCE 200809L
#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <vulkan/vulkan.h>

#define LOCAL 256u

struct push { uint32_t n, salt, iters, pad; };

static double now(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}

static uint32_t rotl(uint32_t v, uint32_t s)
{
	s &= 31u;
	return s == 0u ? v : (v << s) | (v >> (32u - s));
}

static float u2f(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static uint32_t f2u(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

/* CPU copy of the shader up to the shared-memory step. */
static void pre(uint32_t idx, uint32_t seed, const struct push *p, uint32_t *ox, uint32_t *oy, float *of)
{
	uint32_t x = seed ^ p->salt;
	uint32_t y = idx * 0x9e3779b9u + p->salt;
	float f = u2f(0x3f800000u | (x & 0x007fffffu));
	for (uint32_t j = 0; j < p->iters; ++j) {
		x = x * 1664525u + 1013904223u + j;
		x ^= rotl(y + j * 0x45d9f3bu, j);
		y += x ^ (x >> ((j & 7u) + 1u));
		volatile float t = f * 0.99951171875f;     /* two roundings, as the shader's precise math */
		f = t + (float)(y & 255u) * 0.0000019073486328125f;
	}
	*ox = x; *oy = y; *of = f;
}

static uint32_t ulp_distance(uint32_t a, uint32_t b)
{
	int64_t ia = (a & 0x80000000u) ? -(int64_t)(a & 0x7fffffffu) : (int64_t)a;
	int64_t ib = (b & 0x80000000u) ? -(int64_t)(b & 0x7fffffffu) : (int64_t)b;
	int64_t d = ia > ib ? ia - ib : ib - ia;
	return d > 0xffffffffLL ? 0xffffffffu : (uint32_t)d;
}

#define CHECK(call, what) do { VkResult r_ = (call); if (r_ != VK_SUCCESS) { \
	fprintf(stderr, "stone-cu-verify: %s failed (%d)\n", what, (int)r_); return 1; } } while (0)

static int read_file(const char *path, uint32_t **data, size_t *size)
{
	FILE *f = fopen(path, "rb");
	if (!f) return -1;
	if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return -1; }
	long n = ftell(f);
	if (n <= 0 || n % 4) { fclose(f); return -1; }
	rewind(f);
	*data = malloc((size_t)n);
	if (!*data || fread(*data, 1, (size_t)n, f) != (size_t)n) { fclose(f); return -1; }
	fclose(f);
	*size = (size_t)n;
	return 0;
}

static int find_memory(VkPhysicalDevice pd, uint32_t bits, VkMemoryPropertyFlags want)
{
	VkPhysicalDeviceMemoryProperties mp;
	vkGetPhysicalDeviceMemoryProperties(pd, &mp);
	for (uint32_t i = 0; i < mp.memoryTypeCount; ++i)
		if ((bits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & want) == want)
			return (int)i;
	return -1;
}

int main(int argc, char **argv)
{
	const char *spv_path = NULL;
	double seconds = 60;
	uint32_t n = 16u << 20, iters = 64;
	for (int i = 1; i < argc; ++i) {
		if (!strcmp(argv[i], "--seconds") && i + 1 < argc) seconds = atof(argv[++i]);
		else if (!strcmp(argv[i], "--elements") && i + 1 < argc) n = (uint32_t)strtoul(argv[++i], NULL, 10);
		else if (!strcmp(argv[i], "--iters") && i + 1 < argc) iters = (uint32_t)strtoul(argv[++i], NULL, 10);
		else if (!spv_path && argv[i][0] != '-') spv_path = argv[i];
		else { fprintf(stderr, "usage: %s <shader.spv> [--seconds S] [--elements N] [--iters I]\n", argv[0]); return 1; }
	}
	if (!spv_path || n == 0 || n % LOCAL || n > (64u << 20) || iters == 0 || iters > 4096 ||
	    !(seconds > 0 && seconds <= 7200)) {
		fprintf(stderr, "stone-cu-verify: bad arguments (elements: multiple of 256 up to 64M; iters 1-4096; seconds 0-7200)\n");
		return 1;
	}
	uint32_t *spv; size_t spv_size;
	if (read_file(spv_path, &spv, &spv_size)) { fprintf(stderr, "stone-cu-verify: cannot read %s\n", spv_path); return 1; }

	VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "stone-cu-verify",
				  .apiVersion = VK_API_VERSION_1_1 };
	VkInstanceCreateInfo ici = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app };
	VkInstance inst;
	CHECK(vkCreateInstance(&ici, NULL, &inst), "vkCreateInstance");

	uint32_t count = 0;
	CHECK(vkEnumeratePhysicalDevices(inst, &count, NULL), "vkEnumeratePhysicalDevices");
	VkPhysicalDevice devs[16];
	if (count > 16) count = 16;
	CHECK(vkEnumeratePhysicalDevices(inst, &count, devs), "vkEnumeratePhysicalDevices");
	const char *allow_cpu = getenv("STONE_CU_VERIFY_ALLOW_CPU");
	VkPhysicalDevice pd = VK_NULL_HANDLE;
	VkPhysicalDeviceProperties props;
	for (uint32_t i = 0; i < count && pd == VK_NULL_HANDLE; ++i) {
		vkGetPhysicalDeviceProperties(devs[i], &props);
		if (props.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU ||
		    props.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU ||
		    (allow_cpu && !strcmp(allow_cpu, "1") && props.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU))
			pd = devs[i];
	}
	if (pd == VK_NULL_HANDLE) { fprintf(stderr, "stone-cu-verify: no Vulkan GPU found\n"); return 1; }
	printf("device=%s\n", props.deviceName);

	uint32_t qcount = 0, qf = UINT32_MAX;
	vkGetPhysicalDeviceQueueFamilyProperties(pd, &qcount, NULL);
	VkQueueFamilyProperties qprops[16];
	if (qcount > 16) qcount = 16;
	vkGetPhysicalDeviceQueueFamilyProperties(pd, &qcount, qprops);
	for (uint32_t i = 0; i < qcount && qf == UINT32_MAX; ++i)
		if (qprops[i].queueFlags & VK_QUEUE_COMPUTE_BIT) qf = i;
	if (qf == UINT32_MAX) { fprintf(stderr, "stone-cu-verify: no compute queue\n"); return 1; }

	float prio = 1.0f;
	VkDeviceQueueCreateInfo qci = { .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = qf,
					.queueCount = 1, .pQueuePriorities = &prio };
	VkDeviceCreateInfo dci = { .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1,
				   .pQueueCreateInfos = &qci };
	VkDevice dev;
	CHECK(vkCreateDevice(pd, &dci, NULL, &dev), "vkCreateDevice");
	VkQueue queue;
	vkGetDeviceQueue(dev, qf, 0, &queue);

	VkBuffer buf[3];
	VkDeviceMemory mem[3];
	void *map[3];
	for (int i = 0; i < 3; ++i) {
		VkBufferCreateInfo bci = { .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = (VkDeviceSize)n * 4,
					   .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, .sharingMode = VK_SHARING_MODE_EXCLUSIVE };
		CHECK(vkCreateBuffer(dev, &bci, NULL, &buf[i]), "vkCreateBuffer");
		VkMemoryRequirements req;
		vkGetBufferMemoryRequirements(dev, buf[i], &req);
		int mt = find_memory(pd, req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
		if (mt < 0) { fprintf(stderr, "stone-cu-verify: no host-visible memory\n"); return 1; }
		VkMemoryAllocateInfo mai = { .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = req.size,
					     .memoryTypeIndex = (uint32_t)mt };
		CHECK(vkAllocateMemory(dev, &mai, NULL, &mem[i]), "vkAllocateMemory");
		CHECK(vkBindBufferMemory(dev, buf[i], mem[i], 0), "vkBindBufferMemory");
		CHECK(vkMapMemory(dev, mem[i], 0, VK_WHOLE_SIZE, 0, &map[i]), "vkMapMemory");
	}
	uint32_t *seeds = map[0], *out_i = map[1], *out_f = map[2];
	uint32_t s = 0x12345678u;
	for (uint32_t i = 0; i < n; ++i) { s ^= s << 13; s ^= s >> 17; s ^= s << 5; seeds[i] = s; }

	VkDescriptorSetLayoutBinding b[3];
	for (uint32_t i = 0; i < 3; ++i)
		b[i] = (VkDescriptorSetLayoutBinding){ .binding = i, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
						       .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT };
	VkDescriptorSetLayoutCreateInfo dslci = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
						  .bindingCount = 3, .pBindings = b };
	VkDescriptorSetLayout dsl;
	CHECK(vkCreateDescriptorSetLayout(dev, &dslci, NULL, &dsl), "vkCreateDescriptorSetLayout");
	VkPushConstantRange pcr = { .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT, .size = sizeof(struct push) };
	VkPipelineLayoutCreateInfo plci = { .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, .setLayoutCount = 1,
					    .pSetLayouts = &dsl, .pushConstantRangeCount = 1, .pPushConstantRanges = &pcr };
	VkPipelineLayout pl;
	CHECK(vkCreatePipelineLayout(dev, &plci, NULL, &pl), "vkCreatePipelineLayout");
	VkShaderModuleCreateInfo smci = { .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, .codeSize = spv_size, .pCode = spv };
	VkShaderModule sm;
	CHECK(vkCreateShaderModule(dev, &smci, NULL, &sm), "vkCreateShaderModule");
	VkComputePipelineCreateInfo cpci = { .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
		.stage = { .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_COMPUTE_BIT,
			   .module = sm, .pName = "main" }, .layout = pl };
	VkPipeline pipe;
	CHECK(vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &cpci, NULL, &pipe), "vkCreateComputePipelines");

	VkDescriptorPoolSize ps = { .type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 3 };
	VkDescriptorPoolCreateInfo dpci = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, .maxSets = 1,
					    .poolSizeCount = 1, .pPoolSizes = &ps };
	VkDescriptorPool dp;
	CHECK(vkCreateDescriptorPool(dev, &dpci, NULL, &dp), "vkCreateDescriptorPool");
	VkDescriptorSetAllocateInfo dsai = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, .descriptorPool = dp,
					     .descriptorSetCount = 1, .pSetLayouts = &dsl };
	VkDescriptorSet ds;
	CHECK(vkAllocateDescriptorSets(dev, &dsai, &ds), "vkAllocateDescriptorSets");
	VkDescriptorBufferInfo dbi[3];
	VkWriteDescriptorSet w[3];
	for (uint32_t i = 0; i < 3; ++i) {
		dbi[i] = (VkDescriptorBufferInfo){ .buffer = buf[i], .range = VK_WHOLE_SIZE };
		w[i] = (VkWriteDescriptorSet){ .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = ds, .dstBinding = i,
					       .descriptorCount = 1, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
					       .pBufferInfo = &dbi[i] };
	}
	vkUpdateDescriptorSets(dev, 3, w, 0, NULL);

	VkCommandPoolCreateInfo cpi = { .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
					.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, .queueFamilyIndex = qf };
	VkCommandPool cp;
	CHECK(vkCreateCommandPool(dev, &cpi, NULL, &cp), "vkCreateCommandPool");
	VkCommandBufferAllocateInfo cbai = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = cp,
					     .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1 };
	VkCommandBuffer cb;
	CHECK(vkAllocateCommandBuffers(dev, &cbai, &cb), "vkAllocateCommandBuffers");
	VkFenceCreateInfo fci = { .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
	VkFence fence;
	CHECK(vkCreateFence(dev, &fci, NULL, &fence), "vkCreateFence");

	uint32_t *xs = malloc(LOCAL * 4), *ys = malloc(LOCAL * 4), *lds = malloc(LOCAL * 4);
	float *fs = malloc(LOCAL * sizeof(float));
	if (!xs || !ys || !lds || !fs) { fprintf(stderr, "stone-cu-verify: out of memory\n"); return 1; }
	uint64_t total_errors = 0, checked = 0;
	uint32_t pass = 0;
	const uint32_t ulp_tolerance = 0;     /* IEEE multiply and add are exact-rounded on both */
	double start = now();
	do {
		struct push p = { .n = n, .salt = 0x9e3779b9u * (pass + 1u), .iters = iters };
		memset(out_i, 0, (size_t)n * 4);
		memset(out_f, 0, (size_t)n * 4);
		VkCommandBufferBeginInfo cbbi = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
						  .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT };
		CHECK(vkResetCommandBuffer(cb, 0), "vkResetCommandBuffer");
		CHECK(vkBeginCommandBuffer(cb, &cbbi), "vkBeginCommandBuffer");
		vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipe);
		vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pl, 0, 1, &ds, 0, NULL);
		vkCmdPushConstants(cb, pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof p, &p);
		vkCmdDispatch(cb, n / LOCAL, 1, 1);
		VkMemoryBarrier mb = { .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER, .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT,
				       .dstAccessMask = VK_ACCESS_HOST_READ_BIT };
		vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
		CHECK(vkEndCommandBuffer(cb), "vkEndCommandBuffer");
		VkSubmitInfo si = { .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cb };
		double t0 = now();
		CHECK(vkResetFences(dev, 1, &fence), "vkResetFences");
		CHECK(vkQueueSubmit(queue, 1, &si, fence), "vkQueueSubmit");
		/* 60 s: a GPU that does not finish one pass in a minute has hung. */
		CHECK(vkWaitForFences(dev, 1, &fence, VK_TRUE, 60ull * 1000000000ull), "vkWaitForFences (GPU did not finish)");
		double gpu_s = now() - t0;

		uint64_t errors = 0;
		for (uint32_t base = 0; base < n; base += LOCAL) {
			for (uint32_t l = 0; l < LOCAL; ++l) {
				pre(base + l, seeds[base + l], &p, &xs[l], &ys[l], &fs[l]);
				lds[l] = xs[l] ^ ys[l];
			}
			for (uint32_t l = 0; l < LOCAL; ++l) {
				uint32_t idx = base + l;
				uint32_t a = lds[(l * 17u + p.salt) & 255u], bb = lds[(l + 1u) & 255u];
				uint32_t want = (xs[l] ^ rotl(a, l)) + (ys[l] ^ bb);
				int bad = out_i[idx] != want || ulp_distance(out_f[idx], f2u(fs[l])) > ulp_tolerance;
				if (bad && errors < 8)
					fprintf(stderr, "mismatch pass=%u idx=%u workgroup=%u int=0x%08x/0x%08x fp=0x%08x/0x%08x\n",
						pass, idx, idx / LOCAL, out_i[idx], want, out_f[idx], f2u(fs[l]));
				errors += bad;
			}
		}
		printf("pass=%u seconds=%.3f errors=%" PRIu64 "\n", pass, gpu_s, errors);
		fflush(stdout);
		total_errors += errors;
		checked += (uint64_t)n * 2u;
		++pass;
	} while (now() - start < seconds);
	printf("summary passes=%u checked=%" PRIu64 " errors=%" PRIu64 "\n", pass, checked, total_errors);

	vkDestroyFence(dev, fence, NULL);
	vkDestroyCommandPool(dev, cp, NULL);
	vkDestroyDescriptorPool(dev, dp, NULL);
	vkDestroyPipeline(dev, pipe, NULL);
	vkDestroyShaderModule(dev, sm, NULL);
	vkDestroyPipelineLayout(dev, pl, NULL);
	vkDestroyDescriptorSetLayout(dev, dsl, NULL);
	for (int i = 0; i < 3; ++i) {
		vkUnmapMemory(dev, mem[i]);
		vkFreeMemory(dev, mem[i], NULL);
		vkDestroyBuffer(dev, buf[i], NULL);
	}
	vkDestroyDevice(dev, NULL);
	vkDestroyInstance(inst, NULL);
	free(spv); free(xs); free(ys); free(lds); free(fs);
	return total_errors ? 2 : 0;
}
