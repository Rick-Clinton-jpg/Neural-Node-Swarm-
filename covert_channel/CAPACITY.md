# Covert-channel capacity audit

The targeted structural channels are reference selection and order (16.742 bits under a pool of 8 refs) and confidence presence/value (19.932 bits at 6 decimals). Code-selected refs and dropping confidence close those counted choices. The larger total below is about 95% `step_id` string capacity and is not a schema-wide bound.

| Field or stage | Free bits | After both transforms |
|---|---:|---:|
| `required_memory_refs_subset_and_order` | 16.742 | 0.000 |
| `confidence_presence_and_quantized_value` | 19.932 | 0.000 |
| `step_id` | 773.614 | 0.000 |
| `success_criteria_order` | 2.585 | 0.000 |
| `constraints_presence` | 1.000 | 0.000 |
| **Conditional structural total** | **813.873** | **0.000** |

**Scope:** The zero is conditional on fixed public criteria and constraints content, plus the finite, distinct reference pool. It is not a schema-wide bound.
 The unrestricted schema has **unbounded** capacity before and after these transforms because sender-selected list content and length remain.

Canonicalization alone leaves 16.742 bits of reference subset choice in this bounded model. A menu of `k` actions also carries `log2(k)` bits.
