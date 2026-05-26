// Fixture: makes the module's coverage of sample-l3.md complete. EventBus
// has two children in L3 (OrderService, NotificationService); both must
// live inside the fixture for tests that assert "module is fully self-
// contained" (cross_edges == 0). Without NotificationService here, the
// EventBus -> NotificationService edge counted as cross-module.

namespace Acme.Events
{
    public class NotificationService { }
}
