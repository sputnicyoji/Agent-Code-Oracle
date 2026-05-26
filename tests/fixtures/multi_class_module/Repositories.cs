// Fixture: another multi-class file. Holds the IRepository interface and
// two implementors. Used to verify file_to_symbols returns the full set.

namespace Acme.Data
{
    public interface IRepository { }

    public class UserRepository : IRepository { }

    public class OrderRepository : IRepository { }
}
