import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import axios from 'axios';

interface ScanResult {
  is_hate: boolean;
  confidence: number;
  categories: string[];
  explanation: string;
  pending_review_count?: number;
}

interface ScanStatusData {
  status: string;
  messages: string[];
  result?: ScanResult;
  // PATCH: Enhanced metadata stats
  results?: Array<{
    direct_url?: string;
    url_confidence?: number;
    url_source?: string;
    enhanced_metadata?: {
      thread_level?: number;
      is_reply?: boolean;
    };
  }>;
}

const ScanStatus = () => {
  const location = useLocation();
  const [statusData, setStatusData] = useState<ScanStatusData | null>(null);
  const [error, setError] = useState('');
  const scanId = location.state?.scanId;

  useEffect(() => {
    if (!scanId) {
      setError('No scan ID provided');
      return;
    }

    const interval = setInterval(async () => {
      try {
        console.log(`[DEBUG] Polling for scan ID: ${scanId}`);
        const response = await axios.get<ScanStatusData>(`http://localhost:8000/api/scan/${scanId}`);
        console.log('[DEBUG] Received data from backend:', response.data);
        setStatusData(response.data);
        
        if (response.data.status === 'failed') {
          clearInterval(interval);
          const lastMessage = response.data.messages[response.data.messages.length - 1];
          setError(lastMessage || 'Scan failed');
        } else if (response.data.status === 'completed' || response.data.status === 'awaiting_review') {
          clearInterval(interval);
        }
      } catch (err) {
        setError('Failed to get scan status');
        clearInterval(interval);
      }
    }, 2000); // Poll every 2 seconds

    return () => clearInterval(interval);
  }, [scanId]);

  const handleExport = () => {
    // TODO: Implement PDF export functionality
    console.log('Exporting PDF for scan ID:', scanId);
  };

  // PATCH: Calculate URL and thread statistics
  const calculateEnhancedStats = () => {
    if (!statusData?.results || statusData.results.length === 0) {
      return null;
    }

    const totalComments = statusData.results.length;
    const commentsWithUrls = statusData.results.filter(r => r.direct_url && r.direct_url.length > 0).length;
    const avgUrlConfidence = statusData.results
      .filter(r => r.url_confidence && r.url_confidence > 0)
      .reduce((sum, r) => sum + (r.url_confidence || 0), 0) / Math.max(1, commentsWithUrls);
    
    const mainComments = statusData.results.filter(r => r.enhanced_metadata?.thread_level === 0).length;
    const replies = statusData.results.filter(r => r.enhanced_metadata?.thread_level && r.enhanced_metadata.thread_level > 0).length;
    
    const urlSources = statusData.results
      .filter(r => r.url_source)
      .reduce((acc, r) => {
        acc[r.url_source!] = (acc[r.url_source!] || 0) + 1;
        return acc;
      }, {} as Record<string, number>);

    return {
      totalComments,
      commentsWithUrls,
      urlCoveragePercent: totalComments > 0 ? (commentsWithUrls / totalComments) * 100 : 0,
      avgUrlConfidence: avgUrlConfidence || 0,
      mainComments,
      replies,
      urlSources
    };
  };

  const enhancedStats = calculateEnhancedStats();

  console.log('[DEBUG] Rendering with statusData:', statusData);

  if (error) {
    return <div className="text-red-500 text-center p-4">{error}</div>;
  }

  if (!statusData) {
    return <div className="text-center py-4">Loading status...</div>;
  }

  return (
    <div className="max-w-md mx-auto bg-white rounded-xl shadow-md overflow-hidden md:max-w-2xl p-6">
      <h2 className="text-2xl font-bold mb-4">Scan Status</h2>
      <div className="space-y-4">
        <div>
          <span className="font-medium">Scan ID:</span>
          <span className="ml-2 text-gray-600">{scanId}</span>
        </div>
        <div>
          <span className="font-medium">Status:</span>
          <span
            className={`ml-2 font-semibold ${
              statusData.status === 'completed' ? 'text-green-600' : 
              statusData.status === 'failed' ? 'text-red-600' : 'text-blue-600'
            }`}
          >
            {statusData.status}
          </span>
        </div>
        
        <div>
          <p className="font-medium">Progress:</p>
          <ul className="list-disc list-inside bg-gray-50 p-2 rounded">
            {statusData.messages.map((msg, index) => (
              <li key={index} className="text-gray-700">{msg}</li>
            ))}
          </ul>
        </div>

        {/* PATCH: Enhanced metadata statistics */}
        {enhancedStats && (statusData.status === 'completed' || statusData.status === 'awaiting_review') && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <h4 className="text-sm font-medium text-blue-800 mb-3">Extraction Statistics</h4>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-blue-700">
                  <strong>Comments Found:</strong> {enhancedStats.totalComments}
                </p>
                <p className="text-blue-700">
                  <strong>Main Comments:</strong> {enhancedStats.mainComments}
                </p>
                <p className="text-blue-700">
                  <strong>Replies:</strong> {enhancedStats.replies}
                </p>
              </div>
              <div>
                <p className="text-blue-700">
                  <strong>Direct URLs:</strong> {enhancedStats.commentsWithUrls} ({enhancedStats.urlCoveragePercent.toFixed(1)}%)
                </p>
                {enhancedStats.avgUrlConfidence > 0 && (
                  <p className="text-blue-700">
                    <strong>Avg URL Confidence:</strong> {(enhancedStats.avgUrlConfidence * 100).toFixed(1)}%
                  </p>
                )}
                {Object.keys(enhancedStats.urlSources).length > 0 && (
                  <p className="text-blue-700">
                    <strong>URL Sources:</strong> {Object.entries(enhancedStats.urlSources).map(([source, count]) => `${source} (${count})`).join(', ')}
                  </p>
                )}
              </div>
            </div>
          </div>
        )}

        {statusData.status === 'awaiting_review' && (
          <div className="mt-6">
            <h3 className="text-lg leading-6 font-medium text-gray-900">Review Required</h3>
            <div className="mt-2 text-sm text-gray-600">
              <p className="font-semibold text-orange-600">
                {statusData.result?.pending_review_count || 0} potentially offensive comments detected
              </p>
              <p className="mt-2">
                Please review each comment and decide whether to create evidence. You can view the comment content and user information before making your decision.
              </p>
            </div>
            <div className="mt-4">
              <button
                onClick={() => window.location.href = `/review/${scanId}`}
                className="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-orange-600 hover:bg-orange-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500"
              >
                Start Review Process ({statusData.result?.pending_review_count || 0} comments)
              </button>
            </div>
          </div>
        )}

        {statusData.status === 'completed' && (
          <div className="mt-6">
            <h3 className="text-lg leading-6 font-medium text-gray-900">Scan Complete</h3>
            {statusData.result ? (
              <div className="mt-2 text-sm text-gray-600">
                <p className={`font-semibold ${statusData.result.is_hate ? 'text-red-600' : 'text-green-600'}`}>
                  {statusData.result.is_hate ? 'Review Completed' : 'No Hate Speech Detected'}
                </p>
                <p><strong>Explanation:</strong> {statusData.result.explanation || 'No explanation provided.'}</p>
              </div>
            ) : (
              <p className="mt-2 text-sm text-green-600 font-semibold">
                No hate speech was detected during the scan.
              </p>
            )}
            <div className="mt-4">
              <div className="bg-green-50 border border-green-200 rounded-md p-4">
                <div className="flex">
                  <div className="flex-shrink-0">
                    <svg className="h-5 w-5 text-green-400" viewBox="0 0 20 20" fill="currentColor">
                      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                    </svg>
                  </div>
                  <div className="ml-3">
                    <h3 className="text-sm font-medium text-green-800">
                      Process Complete
                    </h3>
                    <div className="mt-2 text-sm text-green-700">
                      <p>
                        {statusData.result?.is_hate 
                          ? "Evidence has been created for approved comments and securely saved."
                          : "Scan completed successfully. No evidence creation needed."
                        }
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
        {statusData.status === 'completed' && (
          <button
            onClick={handleExport}
            disabled // Temporarily disable until backend is implemented
            className="mt-4 w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50"
          >
            Export PDF Evidence (Coming Soon)
          </button>
        )}
      </div>
    </div>
  );
};

export default ScanStatus;


