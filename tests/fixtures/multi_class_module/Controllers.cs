// Fixture: one file declaring three controller types. The bridge's old
// file-stem heuristic would only recognise "Controllers" as internal,
// missing PaymentController/UserController/OrderController entirely.

namespace Acme.Web
{
    public abstract class BaseController { }

    public class PaymentController : BaseController { }

    public class UserController : BaseController { }

    public class OrderController : BaseController { }
}
