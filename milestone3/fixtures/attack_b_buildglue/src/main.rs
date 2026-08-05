// Attack B, COMMITTED (git) source: fully benign. Reads an optional build tag with a safe default.
// option_env! with a default is ordinary Rust; the reviewer correctly passes it. This is exactly what
// is in the repo Kettle builds from, and exactly what an auditor reviews.
fn main() {
    let tag = option_env!("BUILD_TAG").unwrap_or("release");
    println!("greeter v1.0.0 ({})", tag);
}
