# CPU and memory inventories

[Documentation](README.md) · [API](API.md) · [Fidelity](FIDELITY.md)

Synthetic configurations documented on November 8, 2026, enabled by default
for the three profiles. They complement the existing rackhands summaries
without changing the advertised model, CPU count or total RAM.

## Selected configurations

| Profile / model | Installed processors | Cores / threads per CPU | Installed RAM | Effective DDR4 speed | Available physical slots |
|---|---|---|---|---|---|
| Dell PowerEdge R750 | 2 × Xeon Gold 6338 | 32 / 64 | 16 × 32 GiB = 512 GiB | 3,200 MT/s | 32 |
| HPE ProLiant DL380 Gen10 Plus | 2 × Xeon Silver 4314 | 16 / 32 | 16 × 16 GiB = 256 GiB | 2,667 MT/s | 32 |
| Supermicro SYS-6019U-TR4 / X11DPU | 2 × Xeon Gold 6230 | 20 / 40 | 12 × 16 GiB = 192 GiB | 2,933 MT/s | 24 |

These population choices form a test configuration compatible with the
documents below, not a description of an observed machine. Collections list
**installed components only**, not empty slots. RAM is split equally between
the two processors, with one DIMM per channel. DIMMs are DDR4 ECC RDIMMs:
64 data bits on a 72-bit bus. CPUs are simulated with all cores enabled and
Hyper-Threading available.

## Sources and population choices

- **Dell:** the [R750 technical guide, sections 4 and 5](https://i.dell.com/sites/csdocuments/product_docs/en/poweredge-r750-technical-guide.pdf)
  lists the 6338 and DDR4 memory up to 3,200 MT/s. The
  [R750 population rules](https://www.dell.com/support/manuals/en-pa/powerflex-custom-node-r750/vxf-p-owners-guide-r750/general-memory-module-installation-guidelines?guid=guid-6db30d04-e447-48c6-8748-d6389f356fdb&lang=en-us)
  specify the A1–A16 and B1–B16 order. The mock uses A1–A8 and B1–B8,
  giving eight 32 GiB RDIMMs per CPU.
- **HPE:** the [DL380 Gen10 Plus QuickSpecs](https://www.hpe.com/psnow/doc/a50002553enw)
  (revision 41 consulted) list the 4314 and 16 GiB DDR4-3200 RDIMMs,
  including kit P06029-B21. The [Intel Gen10 Plus rules, appendix B, table 4](https://www.hpe.com/psnow/doc/a50003886enw)
  require positions **1, 3, 5, 7, 10, 12, 14, 16** for eight DIMMs per CPU.
  The selected modules are rated for 3,200 MT/s but operate at **2,667 MT/s**,
  the 4314's limit. The kit number is not presented as a part number read
  from a real DIMM.
- **Supermicro:** the [SYS-6019U-TR4 specifications](https://www.supermicro.com/en/products/system/1u/6019/sys-6019u-tr4.php)
  list the X11DPU board, two LGA3647 sockets and DDR4 ECC RDIMMs at 2,933 MT/s.
  The [X11DPU manual, memory section and population table](https://www.supermicro.com/manuals/motherboard/C620/MNL-1865.pdf)
  documents 16 GiB DIMMs and the twelve-module population:
  **P1-DIMMA1 through P1-DIMMF1 and P2-DIMMA1 through P2-DIMMF1**.

CPU specifications also come from Intel product pages:

| CPU | Base frequency in `Model` | Maximum turbo in `MaxSpeedMHz` | `TDPWatts` | Source |
|---|---|---|---|---|
| Gold 6338 | 2.00 GHz | 3,200 MHz | 205 | [Intel 6338](https://www.intel.com/content/www/us/en/products/sku/212285/intel-xeon-gold-6338-processor-48m-cache-2-00-ghz/specifications.html) |
| Silver 4314 | 2.40 GHz | 3,400 MHz | 135 | [Intel 4314](https://www.intel.com/content/www/us/en/products/sku/215269/intel-xeon-silver-4314-processor-24m-cache-2-40-ghz/specifications.html) |
| Gold 6230 | 2.10 GHz | 3,900 MHz | 125 | [Intel 6230](https://www.intel.com/content/www/us/en/products/sku/192437/intel-xeon-gold-6230-processor-27-5m-cache-2-10-ghz/specifications.html) |

Here, `MaxSpeedMHz` represents the vendor's maximum turbo frequency, not a
reading or a guaranteed all-core frequency. `TDPWatts` is a rated specification,
not instantaneous power consumption. No cache, CPUID, serial number, DIMM
manufacturer or part number is invented.

## Redfish responses

The system's `Processors` and `Memory` links lead to collections and their
members. Each member has a stable identity, status, specifications and a link
to the chassis. DIMMs specify their position in `DeviceLocator` and the
associated CPU in `MemoryLocation.Socket` (1 or 2). Supermicro also specifies
channel A–F, numbered 0–5, and the first slot in the channel. Numeric indexing
is a mock convention; a real BMC may use different identifiers and indices.

The standard types used are
[`Processor.v1_7_2`](https://github.com/DMTF/Redfish-Publications/blob/main/json-schema/Processor.v1_7_2.json)
and [`Memory.v1_7_1`](https://github.com/DMTF/Redfish-Publications/blob/main/json-schema/Memory.v1_7_1.json),
also present in the [iLO 5 v3.06 reference](https://servermanagementportal.ext.hpe.com/docs/redfishservices/ilos/ilo5/ilo5_306/ilo5_other_resourcedefns306).
These shared versions do not claim to reproduce the exact schema versions
or all OEM fields in Dell and Supermicro firmware. Despite its name,
`OperatingSpeedMhz` expresses the DDR transfer rate in **MT/s**, as defined by
DMTF. `CapacityMiB` is the capacity in GiB multiplied by 1,024.

Examples:

```sh
curl -u admin:admin 'http://127.0.0.1:8001/redfish/v1/Systems/System.Embedded.1/Processors?$expand=.'
curl -u admin:admin 'http://127.0.0.1:8002/redfish/v1/Systems/1/Memory?$expand=.'
curl -u admin:admin 'http://127.0.0.1:8003/redfish/v1/Systems/1/Memory?$expand=.'
```

## Scope and verification

The additions are made in memory by `redfish_mock/inventory.py`. The rackhands
files remain unchanged; `--raw-fixtures` preserves their empty or missing
inventories. Additions are skipped if a custom dataset changes the model,
CPU, CPU count, RAM or inventory links, or already provides members.
A reset restores the enriched configuration.

HTTP tests verify CPU models, core/thread counts, speeds and TDP, total
capacity per CPU and server, expected slots, unique identifiers, links,
pagination and expansion. They also check rejection of hardware PATCH
requests, restoration after fault injection, and preservation of custom
data and raw mode.

Consistency applies to the initial CPU/RAM inventory. Deliberate injection
through `/_mock/overrides` does not automatically recalculate all summaries
and OEM states. Empty slots, detailed OEM extensions, SPD specifications and
exact firmware behavior remain outside the scope. No comparison with real
hardware has been performed; see the [general report](FIDELITY.md).
