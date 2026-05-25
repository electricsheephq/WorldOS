import Darwin
import Foundation

enum PortFinder {
    static func firstFreePort(startingAt preferredPort: Int) -> Int {
        let start = max(1, min(preferredPort, 65535))
        if isAvailable(start) {
            return start
        }
        let nearbyEnd = min(start + 40, 65535)
        if start < nearbyEnd {
            for port in (start + 1)...nearbyEnd where isAvailable(port) {
                return port
            }
        }
        for port in 8765...8805 where isAvailable(port) {
            return port
        }
        return start
    }

    static func isAvailable(_ port: Int) -> Bool {
        guard (1...65535).contains(port) else { return false }
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(port).bigEndian
        addr.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))

        let result = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        return result == 0
    }
}
