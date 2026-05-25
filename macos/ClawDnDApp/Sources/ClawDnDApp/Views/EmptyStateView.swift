import SwiftUI

struct EmptyStateView: View {
    let title: String
    let symbolName: String

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: symbolName)
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
            Text(title)
                .font(.headline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
