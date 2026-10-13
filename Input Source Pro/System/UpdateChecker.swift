import AppKit
import Foundation

/// Checks the GitHub Releases of this repository for a newer build.
///
/// This is the only feature that contacts the network, and it stays off until the
/// user turns automatic checks on in Settings. Manual checks always contact GitHub.
@MainActor
final class UpdateChecker {
    /// The repository this build updates from.
    static let repository = "azazo1/InputSourcePro"

    private static let automaticChecksKey = "automaticallyChecksForUpdates"

    enum CheckError: LocalizedError {
        case badResponse
        case missingDownloadPage

        var errorDescription: String? {
            switch self {
            case .badResponse:
                return "GitHub did not return a usable release."
            case .missingDownloadPage:
                return "The release does not list a download page."
            }
        }
    }

    struct Release {
        let version: String
        let pageURL: URL
    }

    /// True while an update prompt is on screen. Showing it activates the app, and
    /// that activation must not be mistaken for the user asking for Preferences.
    private(set) var isPrompting = false

    private let defaults: UserDefaults
    private let session: URLSession

    init(defaults: UserDefaults = .standard, session: URLSession = .shared) {
        self.defaults = defaults
        self.session = session
    }

    var automaticallyChecksForUpdates: Bool {
        get { defaults.bool(forKey: Self.automaticChecksKey) }
        set { defaults.set(newValue, forKey: Self.automaticChecksKey) }
    }

    /// Checks `https://api.github.com/repos/<repository>/releases/latest`, which
    /// only reports published, non-prerelease releases.
    func latestRelease() async throws -> Release {
        let url = URL(string: "https://api.github.com/repos/\(Self.repository)/releases/latest")!
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 20)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            throw CheckError.badResponse
        }

        let payload = try JSONDecoder().decode(ReleasePayload.self, from: data)

        guard let pageURL = URL(string: payload.htmlURL) else {
            throw CheckError.missingDownloadPage
        }

        return Release(version: payload.tagName, pageURL: pageURL)
    }

    /// Automatic checks stay silent when the app is current or the request fails,
    /// so a failed check never nags the user. Manual checks always report a result.
    func checkForUpdates(userInitiated: Bool) async {
        let currentVersion = Bundle.main.shortVersion

        do {
            let release = try await latestRelease()

            if Self.isNewer(release.version, than: currentVersion) {
                promptForUpdate(release)
            } else if userInitiated {
                presentAlert(
                    title: "You're up to date",
                    message: String(format: "Version %@ is the latest release.".i18n(), currentVersion)
                )
            }
        } catch {
            guard userInitiated else { return }

            presentAlert(
                title: "Unable to Check for Updates".i18n(),
                message: error.localizedDescription
            )
        }
    }

    /// Compares dotted numeric versions such as `2.13.3` or `v2.13.3`. Text after a
    /// numeric segment is ignored, so `2.13.3-beta-1418` still reads as `2.13.3`.
    static func isNewer(_ candidate: String, than current: String) -> Bool {
        let candidateParts = numericSegments(of: candidate)
        let currentParts = numericSegments(of: current)

        guard !candidateParts.isEmpty, !currentParts.isEmpty else { return false }

        for index in 0..<max(candidateParts.count, currentParts.count) {
            let left = index < candidateParts.count ? candidateParts[index] : 0
            let right = index < currentParts.count ? currentParts[index] : 0

            if left != right {
                return left > right
            }
        }

        return false
    }

    private static func numericSegments(of version: String) -> [Int] {
        let trimmed = version.hasPrefix("v") ? String(version.dropFirst()) : version

        return trimmed
            .split(separator: ".")
            .compactMap { Int($0.prefix(while: \.isNumber)) }
    }

    private func promptForUpdate(_ release: Release) {
        isPrompting = true
        defer { isPrompting = false }

        let alert = NSAlert()
        alert.messageText = "A New Version is Available".i18n()
        alert.informativeText = String(
            format: "Version %@ is available on GitHub. Open the release page to download it?".i18n(),
            release.version
        )
        alert.addButton(withTitle: "Open Release Page".i18n())
        alert.addButton(withTitle: "Later".i18n())

        NSApp.activate(ignoringOtherApps: true)

        if alert.runModal() == .alertFirstButtonReturn {
            NSWorkspace.shared.open(release.pageURL)
        }
    }

    private func presentAlert(title: String, message: String) {
        isPrompting = true
        defer { isPrompting = false }

        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message

        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    private struct ReleasePayload: Decodable {
        let tagName: String
        let htmlURL: String

        enum CodingKeys: String, CodingKey {
            case tagName = "tag_name"
            case htmlURL = "html_url"
        }
    }
}
