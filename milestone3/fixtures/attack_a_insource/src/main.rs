// Attack A main: same visible behavior as clean, but pulls in the committed payload module.
mod payload;

fn main() {
    let _cfg = payload::load_config();
    println!("greeter v1.0.0");
}
