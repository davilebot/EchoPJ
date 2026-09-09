# Cobrança da v2

A v2 possui uma camada própria de pedidos e créditos. O provedor hospeda a tela
de pagamento e nunca envia dados de cartão à aplicação. A integração preparada
usa o Asaas Checkout para Pix e cartão e permanece desativada até a configuração
do catálogo e das credenciais.

## Fluxo

1. Um administrador da organização escolhe uma oferta.
2. A aplicação grava um pedido com referência interna única.
3. O backend cria o Checkout Asaas e devolve apenas a URL HTTPS hospedada.
4. O retorno do navegador informa somente o andamento ao usuário.
5. O saldo e o plano mudam depois de `PAYMENT_RECEIVED`,
   `PAYMENT_CONFIRMED` ou `PAYMENT_RECEIVED_IN_CASH` no webhook.
6. O identificador do pagamento impede crédito duplicado em eventos reenviados.
7. Se o valor recebido divergir do pedido, nada é liberado e o pedido fica
   `needs_review`.

## Configuração

As variáveis abaixo pertencem somente ao serviço v2:

```dotenv
SAAS_BILLING_ENABLED=false
SAAS_BILLING_PROVIDER=asaas
ASAAS_API_URL=https://api-sandbox.asaas.com
ASAAS_API_KEY=
ASAAS_WEBHOOK_TOKEN=
SAAS_BILLING_CATALOG_JSON=[]
```

`ASAAS_WEBHOOK_TOKEN` deve ser um segredo diferente da API Key e ter de 32 a
255 caracteres. O catálogo é uma lista JSON. Este exemplo mostra somente o
formato; preço e créditos devem ser aprovados antes de usar:

```json
[
  {
    "code": "growth",
    "name": "Crescimento",
    "kind": "subscription",
    "price_cents": 14990,
    "credits": 1000,
    "cycle": "MONTHLY",
    "description": "Créditos mensais para prospecção recorrente.",
    "features": ["1.000 créditos por ciclo", "Equipe no mesmo workspace"],
    "highlighted": true
  }
]
```

Valores aceitos em `kind`: `subscription` e `credit_pack`. Uma assinatura
também precisa de `cycle`. Preços são sempre inteiros em centavos.

## Webhook no Asaas

- URL: `https://DOMINIO-DA-V2/api/webhooks/asaas`
- autenticação: o mesmo valor de `ASAAS_WEBHOOK_TOKEN`;
- entrega: sequencial;
- eventos mínimos: `CHECKOUT_PAID`, `CHECKOUT_CANCELED`, `CHECKOUT_EXPIRED`,
  `PAYMENT_RECEIVED`, `PAYMENT_CONFIRMED`, `PAYMENT_OVERDUE`,
  `SUBSCRIPTION_CREATED`, `SUBSCRIPTION_UPDATED`, `SUBSCRIPTION_INACTIVATED` e
  `SUBSCRIPTION_DELETED`.

O endpoint valida o cabeçalho `asaas-access-token`, guarda o hash do payload e
os campos mínimos de conciliação, responde sem registrar o conteúdo completo e
aceita novos campos do provedor.

## Liberação

Antes de mudar `SAAS_BILLING_ENABLED` para `true`:

1. aprovar preços, créditos, validade e regra de renovação;
2. criar a conta e as credenciais Sandbox do Asaas;
3. configurar o webhook com token próprio;
4. executar Pix e cartão de teste e verificar pedido, evento, plano e saldo;
5. repetir o teste de reenvio do mesmo evento;
6. cadastrar as credenciais de produção, trocar `ASAAS_API_URL` e refazer um
   pagamento real controlado.

Boleto exige um fluxo hospedado complementar por Link de Pagamento ou uma
assinatura criada pela API do Asaas. Ele deve ser homologado separadamente antes
de aparecer como opção na interface.
