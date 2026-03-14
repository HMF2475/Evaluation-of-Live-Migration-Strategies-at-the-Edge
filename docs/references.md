# References and Related Work

## Key Papers

### Service Migration
- Satyanarayanan, M. et al. (2009). The Case for VM-Based Cloudlets in Mobile Computing. *IEEE Pervasive Computing*, 8(4), 14–23.
- Ha, K. et al. (2015). Adaptive VM Handoff Across Cloudlets. *Carnegie Mellon University Technical Report CMU-CS-15-113*.
- Shi, W. et al. (2016). Edge Computing: Vision and Challenges. *IEEE Internet of Things Journal*, 3(5), 637–646.

### Container Migration
- Mirkin, A. et al. (2008). Containers Checkpointing and Live Migration. *Proceedings of the Linux Symposium*, 2, 85–90.
- Hines, M. R., & Gopalan, K. (2009). Post-Copy Live Migration of Virtual Machines. *Proceedings of the 2nd ACM SIGOPS/EuroSys European Conference on Computer Systems*.
- Clark, C. et al. (2005). Live Migration of Virtual Machines. *2nd USENIX Symposium on Networked Systems Design and Implementation (NSDI '05)*.

### WebAssembly
- Haas, A. et al. (2017). Bringing the Web up to Speed with WebAssembly. *Proceedings of the 38th ACM SIGPLAN Conference on Programming Language Design and Implementation (PLDI '17)*.
- Jangda, A. et al. (2019). Not So Fast: Analyzing the Performance of WebAssembly vs. Native Code. *2019 USENIX Annual Technical Conference (ATC '19)*.
- Wen, E. et al. (2023). Wasmachine: Bring IoT Applications onto a Novel WebAssembly OS. *IEEE Internet of Things Journal*.

### Tactical Edge Networks
- Bhattacharjee, S. et al. (2017). A tale of two networks: Converging military and commercial networking. *IEEE Communications Magazine*, 55(3), 156–163.
- Roscoe, T. et al. (2009). Challenges of the New Internet. *Dagstuhl Seminar Proceedings*.

### Edge Computing Frameworks
- Oakestra Project: https://oakestra.io — Hierarchical orchestration for edge computing
- WasmEdge: https://wasmedge.org — High-performance WASM runtime for edge/cloud
- CRIU Project: https://criu.org — Checkpoint/Restore in Userspace for Linux

## Tools and Libraries

| Tool        | Purpose                              | Reference |
|-------------|--------------------------------------|-----------|
| Docker      | Container runtime                    | https://docs.docker.com |
| CRIU        | Container checkpoint/restore         | https://criu.org |
| wasmtime    | WASM runtime (Bytecode Alliance)     | https://wasmtime.dev |
| wasmedge    | WASM runtime for edge/cloud          | https://wasmedge.org |
| wasmer      | Universal WASM runtime               | https://wasmer.io |
| psutil      | Python system metrics library        | https://psutil.readthedocs.io |
| matplotlib  | Python plotting library              | https://matplotlib.org |
| iproute2/tc | Linux traffic control                | https://man7.org/linux/man-pages/man8/tc.8.html |
| Rust/WASI   | Systems language with WASI support   | https://doc.rust-lang.org/rustc/platform-support/wasm32-wasip1.html |

## Related Projects

- **Oakestra**: https://github.com/oakestra/oakestra — Edge orchestration framework
  that this project may integrate with for realistic deployment scenarios
- **CRIU-based container migration**: https://github.com/checkpoint-restore
- **WebAssembly at the Edge survey**: https://webassembly.org/docs/use-cases/
