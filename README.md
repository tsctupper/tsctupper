# Tyler Tupper

I'm a full-stack engineer and solo founder. I build construction software for
crews who need to keep a punch list moving even when the job site loses its
connection. I work in TypeScript and React on the interface and PostgreSQL on
the data model and access rules.

## Open source · otpkit

I wrote [otpkit](https://github.com/tsctupper/otpkit) for Node.js applications
that use authenticator codes at sign-in. It generates time-based and counter-based
codes, builds enrollment URIs, and uses Node's built-in crypto with zero runtime
dependencies.

One design choice matters when a user submits the same code twice. A six-digit
code can still be valid on the second try. The library's `matchTotp` function
returns the time step that matched, so the application can record which step
it has already accepted.

```sh
npm install @tsctupper/otpkit
```

[Try the quick start](https://github.com/tsctupper/otpkit#quick-start) ·
[Read the standards tests](https://github.com/tsctupper/otpkit/blob/main/test/rfc.test.ts) ·
[Browse releases](https://github.com/tsctupper/otpkit/releases)
