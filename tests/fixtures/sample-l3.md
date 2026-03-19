# Sample Repo Map (L3)

## Reference Graph

BaseController (refs: 45, rank: 0.05)
  <- PaymentController (inherits)
  <- UserController (inherits)
  <- OrderController (inherits)

IRepository (refs: 30, rank: 0.03)
  <- UserRepository (implements)
  <- OrderRepository (implements)

EventBus (refs: 25, rank: 0.02)
  <- OrderService (inherits)
  <- NotificationService (inherits)

BaseService (refs: 20, rank: 0.02)
  <- PaymentGateway (inherits)
  <- InvoiceGenerator (inherits)
  <- AuthMiddleware (inherits)
