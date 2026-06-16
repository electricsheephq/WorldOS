import Darwin
import Foundation
import XCTest

@testable import WorldOSApp

/// Unit tests for `PortFinder` availability + selection logic.
///
/// Hermetic: the only sockets bound are loopback (`127.0.0.1`) listeners this test opens and
/// closes itself, within the test process. No outbound network, no app launch.
final class PortFinderTests: XCTestCase {

    // MARK: - isAvailable: range guards

    func testIsAvailableRejectsPortZero() {
        XCTAssertFalse(PortFinder.isAvailable(0))
    }

    func testIsAvailableRejectsNegativePort() {
        XCTAssertFalse(PortFinder.isAvailable(-1))
    }

    func testIsAvailableRejectsAbove65535() {
        XCTAssertFalse(PortFinder.isAvailable(65536))
        XCTAssertFalse(PortFinder.isAvailable(99999))
    }

    // MARK: - isAvailable: real binding

    /// A port that nobody is listening on should report available. We pick a free port by binding
    /// then releasing, then assert `isAvailable` agrees once it's released.
    func testIsAvailableReportsTrueForAFreePort() {
        guard let free = bindEphemeralThenRelease() else {
            XCTFail("could not determine a free loopback port")
            return
        }
        XCTAssertTrue(PortFinder.isAvailable(free), "a released port should be reported available")
    }

    /// An OCCUPIED port must report unavailable. We hold a live loopback listener on a concrete
    /// port for the duration of the assertion, then release it.
    func testIsAvailableReportsFalseForAnOccupiedPort() {
        guard let (fd, port) = openLoopbackListener() else {
            XCTFail("could not open a loopback listener to occupy a port")
            return
        }
        defer { close(fd) }
        XCTAssertFalse(
            PortFinder.isAvailable(port),
            "a port held by a live bound listener must be reported unavailable"
        )
    }

    // MARK: - firstFreePort

    /// When the preferred port is free, it is returned as-is.
    func testFirstFreePortReturnsPreferredWhenFree() {
        guard let free = bindEphemeralThenRelease() else {
            XCTFail("could not determine a free loopback port")
            return
        }
        XCTAssertEqual(PortFinder.firstFreePort(startingAt: free), free)
    }

    /// When the preferred port is occupied, the finder must NOT return it; it must hand back a
    /// different, actually-available port (from the nearby window or the 8765-8805 fallback band).
    func testFirstFreePortSkipsOccupiedPreferred() {
        guard let (fd, port) = openLoopbackListener() else {
            XCTFail("could not open a loopback listener to occupy a port")
            return
        }
        defer { close(fd) }
        let chosen = PortFinder.firstFreePort(startingAt: port)
        XCTAssertNotNil(chosen)
        if let chosen {
            XCTAssertNotEqual(chosen, port, "must not return the occupied preferred port")
            XCTAssertTrue(PortFinder.isAvailable(chosen), "returned port must actually be free")
            XCTAssertTrue((1...65535).contains(chosen), "returned port must be in valid range")
        }
    }

    /// A nonsensical preferred port is clamped into the valid range and still yields a usable port.
    func testFirstFreePortClampsOutOfRangePreferred() {
        let chosen = PortFinder.firstFreePort(startingAt: 0)
        XCTAssertNotNil(chosen, "should still find a free port even from a clamped start")
        if let chosen {
            XCTAssertTrue((1...65535).contains(chosen))
            XCTAssertTrue(PortFinder.isAvailable(chosen))
        }
    }

    // MARK: - Test helpers (loopback only, fully cleaned up)

    /// Open a real `bind()`+`listen()` loopback listener on an OS-chosen ephemeral port. Returns
    /// the live fd (caller must close) and the concrete port number it landed on, so the test can
    /// assert against a port that is genuinely occupied for the lifetime of the fd.
    private func openLoopbackListener() -> (fd: Int32, port: Int)? {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = 0  // ask the kernel for an ephemeral port
        addr.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))

        let bindResult = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0, listen(fd, 1) == 0 else {
            close(fd)
            return nil
        }

        var bound = sockaddr_in()
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        let nameResult = withUnsafeMutablePointer(to: &bound) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                getsockname(fd, $0, &len)
            }
        }
        guard nameResult == 0 else {
            close(fd)
            return nil
        }
        let port = Int(UInt16(bigEndian: bound.sin_port))
        return (fd, port)
    }

    /// Bind an ephemeral loopback port, learn its number, then release it — yielding a port that
    /// was free a moment ago (best-effort "free port" probe for the available-path tests).
    private func bindEphemeralThenRelease() -> Int? {
        guard let (fd, port) = openLoopbackListener() else { return nil }
        close(fd)
        return port
    }
}
