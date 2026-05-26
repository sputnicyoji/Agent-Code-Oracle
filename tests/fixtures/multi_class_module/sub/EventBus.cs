// Fixture: single-class file in a nested directory. Used to verify the
// walker descends and stops correctly, and to give R2 (single-dir
// rationale) at least one path in a distinct directory.

namespace Acme.Events
{
    public class EventBus { }

    public class OrderService { }
}
