КАТАЛОГ ОШИБОК
|ID| Условие                                         |  Код ошибки             |
|-|-------------------------------------------------|--------------------------------------------------------------------------|
|V-1| обязательное поле пустое                        |                                     VALIDATION_ERROR       |
|V-2| order\_id не по ^ord_[0-9]{4}$ |                                         INVALID_FORMAT         |
|V-3| amount ≤ 0 или > 1 000 000                      |                                         AMOUNT_OUT_OF_RANGE    |
|V-4| currency не в \[RUB]                            |                                          UNSUPPORTED_CURRENCY   |
|V-5| recipient не в recipients.csv или INACTIVE      |                                          RECIPIENT_NOT_FOUND    |
|V-6| purpose > 200 сим. или содержит PAN (\d{13,19}) |                                       INVALID_PURPOSE        |
|V-7| amount содержит больше 2 знаков после точки     |                                          INVALID_AMOUNT_PRECISION |
|V-8| пара order_id + recipient уже существует        | DUPLICATE_ORDER        |
|V-9| повтор Idempotency-Key                          |       DUPLICATE_REQUEST
|V-10| На карте пациента недостаточно средств| INSUFFICIENT_FUNDS|
|V-11|Банк отклонил операцию без указания точной причины| CARD_DECLINED|
|V-12| Платежный шлюз не ответил за отведенное время| GATEWAY_TIMEOUT|
|V-13| Банк временно недоступен | ACQUIER_UNAVAILABLE|