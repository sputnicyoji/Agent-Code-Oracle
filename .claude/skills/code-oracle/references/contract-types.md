# Contract Types

> 5 types of implicit knowledge, ranked by AI blind spot severity

## Priority Table

| Type | AI Self-Inference | Oracle Value | Priority |
|------|------------------|-------------|----------|
| `blast_radius` | **Very Weak** | Extremely High | **P0** |
| `rationale` | **Weak** | High | **P1** |
| `data_flow` | **Medium** | Medium | **P2** |
| `ordering` | **Strong** | Low-Medium | P3 |
| `thread_safety` | **Strong** | Low | P3 |

## blast_radius (P0)

**What:** Who downstream consumes this module's output? What breaks if you change the output format?

**Example:**
```json
{
  "type": "blast_radius",
  "title": "Producer output affects downstream consumers",
  "description": "A producer emits a result shape consumed by downstream invoice and audit pipelines",
  "blind_spot": "Developer modifying a result structure only considers producer internals",
  "violation_consequence": "Downstream consumers lose or misread fields"
}
```

## rationale (P1)

**What:** Why was this designed this way? What business/technical constraint drove the decision?

**Example:**
```json
{
  "type": "rationale",
  "title": "EventBus uses synchronous dispatch intentionally",
  "description": "EventBus dispatches synchronously despite async being available, to ensure event ordering guarantees required by the audit trail",
  "blind_spot": "Developer may convert to async for performance without understanding ordering requirement",
  "violation_consequence": "Audit trail events arrive out of order, compliance violation"
}
```

## data_flow (P2)

**What:** How does data flow through the system? What are the validity windows?

**Example:**
```json
{
  "type": "data_flow",
  "title": "UserSession data valid only during request lifecycle",
  "description": "UserSession is populated by AuthMiddleware, cached in RequestContext, and invalidated at response completion",
  "blind_spot": "Accessing UserSession in a background job returns stale/null data",
  "violation_consequence": "Permission checks fail silently in async workers"
}
```

## ordering (P3)

**What:** What must happen before/after what?

**Example:**
```json
{
  "type": "ordering",
  "title": "DatabaseMigration must complete before SchemaValidator",
  "description": "SchemaValidator reads schema version set by DatabaseMigration during startup",
  "blind_spot": "Reordering init sequence seems safe but breaks validation",
  "violation_consequence": "SchemaValidator reads stale version, reports false errors"
}
```

## thread_safety (P3)

**What:** What concurrent access patterns exist?

**Example:**
```json
{
  "type": "thread_safety",
  "title": "ConnectionPool uses ConcurrentQueue for thread safety",
  "description": "ConnectionPool chose ConcurrentQueue over List to handle concurrent checkout/return",
  "blind_spot": "Replacing with List for simplicity introduces race conditions",
  "violation_consequence": "Connection leaks and double-disposal under load"
}
```

## Quality Gate

**blast_radius + rationale must be >= 50% of total contracts.** If your scan produces mostly ordering/thread_safety, Round 3 filtering was too lenient.

