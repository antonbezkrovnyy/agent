ARG RUST_BUILD_IMAGE=rustlang/rust:nightly-bullseye
FROM ${RUST_BUILD_IMAGE} AS build
WORKDIR /build
COPY mpsiem /build/mpsiem
COPY validator /build/validator
WORKDIR /build/validator
RUN cargo build --release

FROM debian:bullseye-slim
COPY --from=build /build/validator/target/release/mp_rule_validator /usr/local/bin/mp-rule-validator
ENTRYPOINT ["/usr/local/bin/mp-rule-validator"]
